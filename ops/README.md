# Kronika — Local Operations & Quickstart

This directory contains container configurations and data seed utilities for running DataHub locally.

## Components

- `docker-compose.yml`: DataHub quickstart container setup.
- `seed_healthcare.py`: Generates the canonical sample healthcare dataset (`raw_patients`, `staging_patients`, `mart_billing`, `mart_demographics`).

## Setup

```bash
docker compose up -d
python seed_healthcare.py
```
