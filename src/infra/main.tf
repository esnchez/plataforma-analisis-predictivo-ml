// Infraestructura de datos en AWS: bucket S3 (raw, features, models, predictions) y tabla DynamoDB
// con el último score de churn por cliente. Debe coincidir con src/churn/localdev/setup_resources.py, que crea
// lo mismo en local (LocalStack y dynamodb-local). El cómputo (servicios training, scoring y API) aún
// no está aquí: ver docs/production.md.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "region" {
  type    = string
  default = "us-west-2"
}

variable "bucket_name" {
  type    = string
  default = "saas-churn-data"
}

variable "table_name" {
  type    = string
  default = "ChurnPredictions"
}

provider "aws" {
  region = var.region
}

resource "aws_s3_bucket" "data_bucket" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_public_access_block" "data_bucket" {
  bucket                  = aws_s3_bucket.data_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data_bucket" {
  bucket = aws_s3_bucket.data_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

// Versioning protects models/ and raw/ from accidental overwrites or deletes.
resource "aws_s3_bucket_versioning" "data_bucket" {
  bucket = aws_s3_bucket.data_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_dynamodb_table" "churn_predictions" {
  name         = var.table_name
  hash_key     = "CustomerID"
  billing_mode = "PAY_PER_REQUEST"

  attribute {
    name = "CustomerID"
    type = "S"
  }

  attribute {
    name = "risk_band"
    type = "S"
  }

  attribute {
    name = "churn_probability"
    type = "N"
  }

  // "Clientes en riesgo alto, ordenados por probabilidad" sin escanear la tabla.
  global_secondary_index {
    name            = "RiskBandIndex"
    hash_key        = "risk_band"
    range_key       = "churn_probability"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }
}

output "bucket_name" {
  value = aws_s3_bucket.data_bucket.bucket
}

output "table_name" {
  value = aws_dynamodb_table.churn_predictions.name
}
