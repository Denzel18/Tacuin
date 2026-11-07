# Tacuìn su Google Cloud: una VM a Milano, raggiungibile solo in HTTPS (80/443).
# SSH solo attraverso IAP (Identity-Aware Proxy): la porta 22 NON è aperta su internet,
# si entra con il proprio account Google (OS Login). Backup notturni in un bucket in cui la VM può solo scrivere.

locals {
  tag      = "${var.name}-web"
  services = ["cloudresourcemanager.googleapis.com", "iam.googleapis.com", "compute.googleapis.com", "iap.googleapis.com", "oslogin.googleapis.com", "storage.googleapis.com", "logging.googleapis.com", "monitoring.googleapis.com"]
}

data "google_project" "this" {}

resource "google_project_service" "api" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------- rete
resource "google_compute_network" "vpc" {
  name                    = "${var.name}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.api]
}

resource "google_compute_subnetwork" "subnet" {
  name                     = "${var.name}-subnet"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = "10.10.0.0/24"
  private_ip_google_access = true
}

# Solo il sito: HTTP (per il certificato Let's Encrypt e il redirect) e HTTPS
resource "google_compute_firewall" "web" {
  name          = "${var.name}-allow-web"
  network       = google_compute_network.vpc.name
  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = [local.tag]
  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
  allow {
    protocol = "udp"
    ports    = ["443"]
  }
}

# SSH solo dagli indirizzi di IAP (35.235.240.0/20): da internet la porta 22 resta chiusa
resource "google_compute_firewall" "ssh_iap" {
  name          = "${var.name}-allow-ssh-iap"
  network       = google_compute_network.vpc.name
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = [local.tag]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_address" "ip" {
  name         = "${var.name}-ip"
  region       = var.region
  network_tier = "PREMIUM"
  depends_on   = [google_project_service.api]
}

# ---------------------------------------------------------------- identità della VM (permessi minimi)
resource "google_service_account" "vm" {
  account_id   = "${var.name}-vm"
  display_name = "Tacuìn VM"
  depends_on   = [google_project_service.api]
}

resource "google_project_iam_member" "vm_logs" {
  for_each = toset(["roles/logging.logWriter", "roles/monitoring.metricWriter"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.vm.email}"
}

# ---------------------------------------------------------------- backup
resource "google_storage_bucket" "backup" {
  name                        = "${var.project_id}-${var.name}-backup"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  soft_delete_policy {
    retention_duration_seconds = 604800 # 7 giorni per recuperare un file cancellato per errore
  }

  dynamic "lifecycle_rule" {
    for_each = var.backup_retention_days > 0 ? [1] : []
    content {
      condition {
        age = var.backup_retention_days
      }
      action {
        type = "Delete"
      }
    }
  }

  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 30
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.api]
}

# La VM può solo CREARE oggetti: non può leggere, sovrascrivere né cancellare i backup.
# Se il server venisse compromesso, i backup restano al sicuro.
resource "google_storage_bucket_iam_member" "vm_backup_writer" {
  bucket = google_storage_bucket.backup.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.vm.email}"
}

# ---------------------------------------------------------------- snapshot giornalieri del disco
resource "google_compute_resource_policy" "snapshots" {
  name   = "${var.name}-snapshot-giornaliero"
  region = var.region
  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "02:00"
      }
    }
    retention_policy {
      max_retention_days    = var.snapshot_retention_days
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }
    snapshot_properties {
      storage_locations = [var.region]
      labels = {
        app = var.name
      }
    }
  }
  depends_on = [google_project_service.api]
}

# ---------------------------------------------------------------- la VM
resource "google_compute_instance" "vm" {
  name                      = var.name
  machine_type              = var.machine_type
  zone                      = var.zone
  tags                      = [local.tag]
  allow_stopping_for_update = true
  deletion_protection       = var.deletion_protection
  labels = {
    app = var.name
  }

  boot_disk {
    initialize_params {
      image = var.image
      size  = var.disk_size_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.subnet.id
    access_config {
      nat_ip       = google_compute_address.ip.address
      network_tier = "PREMIUM"
    }
  }

  service_account {
    email  = google_service_account.vm.email
    scopes = ["cloud-platform"] # i permessi veri sono quelli IAM qui sopra (minimi)
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    enable-oslogin         = "TRUE" # SSH con l'account Google, niente chiavi sparse
    block-project-ssh-keys = "TRUE"
  }

  depends_on = [google_project_iam_member.vm_logs]
}

resource "google_compute_disk_resource_policy_attachment" "snapshots" {
  name = google_compute_resource_policy.snapshots.name
  disk = google_compute_instance.vm.name # il disco di sistema ha lo stesso nome della VM
  zone = var.zone
}

# ---------------------------------------------------------------- chi può entrare in SSH (oltre ai proprietari)
resource "google_project_iam_member" "admin_iap" {
  for_each = toset(var.admin_members)
  project  = var.project_id
  role     = "roles/iap.tunnelResourceAccessor"
  member   = each.value
}

resource "google_project_iam_member" "admin_oslogin" {
  for_each = toset(var.admin_members)
  project  = var.project_id
  role     = "roles/compute.osAdminLogin"
  member   = each.value
}

resource "google_project_iam_member" "admin_viewer" {
  for_each = toset(var.admin_members)
  project  = var.project_id
  role     = "roles/compute.viewer"
  member   = each.value
}

resource "google_service_account_iam_member" "admin_sa_user" {
  for_each           = toset(var.admin_members)
  service_account_id = google_service_account.vm.name
  role               = "roles/iam.serviceAccountUser"
  member             = each.value
}

# ---------------------------------------------------------------- avviso di spesa (facoltativo)
resource "google_billing_budget" "budget" {
  count           = var.billing_account == "" ? 0 : 1
  billing_account = var.billing_account
  display_name    = "${var.name} - avviso di spesa"
  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }
  amount {
    specified_amount {
      units = tostring(var.budget_amount)
    }
  }
  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
}
