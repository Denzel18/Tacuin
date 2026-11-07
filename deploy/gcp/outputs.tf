output "ip" {
  description = "IP pubblico statico della VM: crea un record DNS A che punta qui"
  value       = google_compute_address.ip.address
}

output "vm_name" {
  value = google_compute_instance.vm.name
}

output "zone" {
  value = var.zone
}

output "backup_bucket" {
  description = "Bucket dei backup notturni (la VM può solo scriverci)"
  value       = google_storage_bucket.backup.name
}

output "ssh" {
  description = "Come entrare nella VM (solo tramite IAP, con il tuo account Google)"
  value       = "gcloud compute ssh ${google_compute_instance.vm.name} --zone ${var.zone} --tunnel-through-iap --project ${var.project_id}"
}
