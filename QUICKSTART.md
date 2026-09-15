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

## Step 2: Find Companies

```bash
# From a news search (finds real companies from RSS results)
uv run bam discover --source rss --query "accounting firms" --limit 10

# From a list of URLs
uv run bam discover --urls https://acme.test https://beta.test

# From a CSV file (columns: name, url, domain, industry)
uv run bam discover --csv companies.csv
```

Denylisted domains are filtered automatically.

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

# What should I do next?
uv run bam next

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
