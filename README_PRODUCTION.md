# BAM - Business Automation Machine

**Version:** 0.1.0  
**Status:** Production Ready

A local-first prospect-to-delivery operating system for a one-person tech business.

## What BAM Does

BAM helps you:
1. Research prospects from their websites
2. Score and qualify opportunities
3. Manage leads through a sales pipeline
4. Deliver PDF→Excel and Excel Cleaner services
5. Track revenue and payments
6. Maintain a complete audit trail

## Quick Start

```bash
# 1. Check everything is working
uv run bam doctor

# 2. Research a lead
uv run bam research https://example.com

# 3. List your leads
uv run bam leads

# 4. Approve a lead for contact
uv run bam approve 1 -y

# 5. Record contact
uv run bam contact 1 contacted

# 6. Deliver a service
uv run bam deliver file.pdf --service pdf-to-excel --client "Client Name" --agreed 100

# 7. Record payment
uv run bam pay 1 --amount 100

# 8. Check your business metrics
uv run bam digest
```

Or use the launcher:
```bash
BAM.bat
```

## Commands

| Command | Description |
|---------|-------------|
| `bam doctor` | Check system health |
| `bam research <url>` | Research a prospect |
| `bam leads` | List all leads |
| `bam approve <id>` | Approve lead for contact |
| `bam contact <id> <outcome>` | Record contact outcome |
| `bam deliver <file>` | Execute a service |
| `bam jobs` | List all jobs |
| `bam pay <id>` | Record payment |
| `bam digest` | Business metrics |
| `bam backup` | Backup database |
| `bam restore <path>` | Restore from backup |
| `bam services` | Service status |

## Services

### PDF → Excel
- **Service ID:** `pdf-to-excel@1`
- **Input:** PDF files
- **Output:** Excel/CSV with validation report
- **Use case:** Extract tables from PDF documents

### Excel Cleaner
- **Service ID:** `excel-cleaner@1`
- **Input:** CSV/Excel files or directories
- **Output:** Cleaned files with audit report
- **Use case:** Clean and validate spreadsheet data

### QA Automation
- **Service ID:** `qa-agent@0`
- **Status:** Disabled (Phase 2)

## Directory Structure

```
BAM/
├── bam/                    # Core Python package
├── config/                 # Configuration files
│   ├── config.yaml        # Runtime settings
│   ├── service-registry.yaml  # Service definitions
│   └── weights.yaml       # Scoring weights
├── data/                   # Runtime data
│   ├── bam.db            # SQLite database
│   ├── leads/            # Lead research artifacts
│   ├── jobs/             # Job sandbox directories
│   └── runs/             # Run manifests
├── backups/               # Database backups
├── tests/                 # Test suite
├── docs/                  # Documentation
├── BAM.bat               # Windows launcher
└── README.md             # This file
```

## Configuration

### config/config.yaml
- **Database path:** `data/bam.db`
- **LLM:** Disabled by default (deterministic mode)
- **Fetch limits:** 1 page, 2MB max, 15s timeout

### config/service-registry.yaml
- Defines available services and their contracts
- Frozen contract versions prevent breaking changes

### config/weights.yaml
- Scoring dimensions and thresholds
- Auto-queue threshold: 70
- Review threshold: 50

## Backup

```bash
# Create backup
uv run bam backup

# Restore from backup
uv run bam restore backups/bam_2026-09-15.db
```

Backups are stored in `backups/` with timestamps and SHA-256 hashes.

## Recovery

If BAM fails:
1. Run `uv run bam doctor` to diagnose
2. Check `data/bam.db` exists and is accessible
3. Restore from backup if needed: `uv run bam restore <backup_path>`
4. Run `uv run bam services` to verify services

## Architecture

- **Local-first:** All data stored locally in SQLite
- **Zero-cloud:** No external data transmission
- **Deterministic:** Services produce consistent results
- **Audited:** Every action logged
- **Human approval:** Required for contact and critical decisions

## Testing

```bash
# Run all tests
uv run pytest

# Run specific test
uv run pytest tests/test_store.py
```

## Support

- Check `uv run bam doctor` for system status
- Review `data/bam.db` for audit trail
- Check `backups/` for database backups
