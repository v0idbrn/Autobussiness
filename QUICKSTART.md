# BAM Quick Start

**Get productive in 5 minutes.**

## Step 1: Check It Works

```bash
uv run bam doctor
```

You should see:
```
config          : ok
database        : data\bam.db
runtime deps    : httpx, pyyaml (only)
service pdf-to-excel  : ok
service excel-cleaner : ok
```

## Step 2: Seed Queries (First Time Only)

```bash
# Seed the query catalog with proven search phrases
uv run bam bootstrap

# Or seed for a specific service
uv run bam bootstrap --service pdf_to_excel

# Import leads from a CSV file
uv run bam bootstrap --import-csv leads.csv

# Preview without writing
uv run bam bootstrap --dry-run
```

The bootstrap seeds 51 queries across all services. This gives `bam hunt`
immediate search terms on first run.

## Step 3: Daily Hunt (Recommended)

```bash
# One command: discover → research → qualify → opportunities
uv run bam hunt

# Specific service:
uv run bam hunt --service pdf_to_excel

# Multiple services:
uv run bam hunt --service pdf_to_excel excel_cleaning
```

`bam hunt` automatically:
- Selects best queries from learning history (or seed queries on cold start)
- Discovers candidates from job boards + remote boards + news RSS
- Researches top candidates (fetches pages, extracts signals)
- Creates opportunities with intent scoring
- Shows top 5 opportunities with WHY, CONTACT, OFFER, ACTION

## Step 4: Manual Discovery (Alternative)

```bash
# From a news search (finds real companies from RSS results)
uv run bam discover --source rss --query "accounting firms" --limit 10

# Find PUBLIC EXPRESSIONS OF NEED (hiring posts, help requests)
uv run bam discover --intent --campaign pdf_to_excel --limit 10

# Custom intent query (finds people asking for specific help)
uv run bam discover --intent --intent-query "help converting PDF to Excel"

# Mine a curated directory for leads (weak/medium intent — proves existence, not need)
uv run bam discover --intent --directory "https://<directory-url>" --limit 10

# From a list of URLs
uv run bam discover --urls https://acme.test https://beta.test

# From a CSV file (columns: name, url, domain, industry)
uv run bam discover --csv companies.csv
```

Denylisted domains are filtered automatically. Directory companies get LEAD
SIGNAL (existence proof), not automatic commercial intent.

## Step 3: Research a Prospect

```bash
uv run bam research https://example.com
```

Research covers the homepage plus up to 4 subpages (`/about`, `/services`,
`/team`, `/contact` and any matching nav links). Every page becomes SHA-256
evidence; claims record which page they came from.

## Step 4: Review, Approve, Contact

```bash
# See all leads
uv run bam leads

# What should I do next? (HOT OPPORTUNITIES → CONTACT-READY → FOLLOW-UPS)
uv run bam next

# See all open opportunities (explicit > strong > medium, by freshness)
uv run bam opportunities

# Campaign funnel + source/query quality
uv run bam campaign-report

# Approve outreach (HUMAN decision)
uv run bam approve-contact 1 -y

# Generate the outreach message
uv run bam draft-outreach 1            # print it
uv run bam draft-outreach 1 --mailto   # open it in your email client
uv run bam draft-outreach 1 --file     # save as .eml under data/drafts/

# You send it yourself. BAM never sends anything.

# Record the outcome
uv run bam contact 1 contacted
```

Follow-ups are created automatically: after first contact (+3 days), after a
reply (+7 days), when a quote is approved (+5 days), and after delivery (+30
days for repeat business).

```bash
uv run bam followups            # pending follow-ups
uv run bam followups --overdue  # only overdue
```

## Step 5: Deliver Service

```bash
# PDF to Excel
uv run bam deliver invoice.pdf --service pdf-to-excel --client "ACME Corp" --agreed 150

# Excel Cleaner
uv run bam deliver data.csv --service excel-cleaner --client "ACME Corp" --agreed 75
```

## Step 6: Get Paid and Record It

```bash
uv run bam pay 1 --amount 150
```

## Step 7: Check Your Numbers

```bash
uv run bam digest
```

## That's It

You've completed a full sales cycle: discover → research → approve → outreach →
deliver → get paid. Repeat.

## Need Help?

```bash
# Check system health
uv run bam doctor

# See all commands
uv run bam --help

# Backup your data (self-verifying; keeps the last 5 by default,
# prunes older ones automatically — see backup.keep in config/config.yaml)
uv run bam backup
```
