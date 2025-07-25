variable "prefix" {
  description = "Prefix for all resources"
  type        = string
  default     = "databricks-chatx"
}

variable "bot_name" {
  description = "Name of the bot"
  type        = string
  default     = "Databricks ChatX"
}

variable "location" {
  description = "Location for all resources"
  type        = string
}

variable "subscription_id" {
  description = "Subscription ID for the Azure subscription"
  type        = string
}

variable "tenant_id" {
  description = "Tenant ID for the Azure Active Directory"
  type        = string
}

variable "databricks_host" {
  description = "Databricks host URL"
  type        = string
}

variable "databricks_profile" {
  description = "Databricks profile for authentication. This must be at account-level profile"
  type        = string
}
