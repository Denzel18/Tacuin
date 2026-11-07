variable "project_id" {
  description = "ID del progetto Google Cloud (es. tacuin-123456)"
  type        = string
}

variable "region" {
  description = "Regione: europe-west8 = Milano"
  type        = string
  default     = "europe-west8"
}

variable "zone" {
  description = "Zona dentro la regione"
  type        = string
  default     = "europe-west8-a"
}

variable "name" {
  description = "Nome della VM e prefisso delle risorse"
  type        = string
  default     = "tacuin"
}

variable "machine_type" {
  description = "e2-small (2 GB) basta per Tacuìn + scansione scontrini; e2-micro (1 GB) solo senza scontrini"
  type        = string
  default     = "e2-small"
}

variable "disk_size_gb" {
  description = "Disco di sistema e dati (GB)"
  type        = number
  default     = 20
}

variable "image" {
  description = "Immagine del sistema operativo"
  type        = string
  default     = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
}

variable "admin_members" {
  description = "Chi può entrare in SSH (via IAP), es. [\"user:tuo@gmail.com\"]. I proprietari del progetto possono già."
  type        = list(string)
  default     = []
}

variable "backup_retention_days" {
  description = "Dopo quanti giorni i backup nel bucket vengono cancellati (0 = mai)"
  type        = number
  default     = 365
}

variable "snapshot_retention_days" {
  description = "Quanti giorni tenere gli snapshot automatici del disco"
  type        = number
  default     = 14
}

variable "deletion_protection" {
  description = "Impedisce di cancellare la VM per errore (anche con terraform destroy)"
  type        = bool
  default     = true
}

variable "billing_account" {
  description = "Facoltativo: ID dell'account di fatturazione (XXXXXX-XXXXXX-XXXXXX) per creare un avviso di spesa"
  type        = string
  default     = ""
}

variable "budget_amount" {
  description = "Avviso di spesa mensile (nella valuta dell'account di fatturazione)"
  type        = number
  default     = 30
}
