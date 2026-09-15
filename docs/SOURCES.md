# BAM Source Registry

BAM discovers commercial intent from public sources. All sources are
fully public, legitimate, and respect robots.txt / rate limits.

## Source Types

| Type | Description | Intent Level |
|------|-------------|--------------|
| `job_board:wpjobs` | WordPress Jobs RSS | MEDIUM (hiring signal) |
| `job_board:wwr_*` | We Work Remotely RSS | MEDIUM (hiring signal) |
| `job_board:remoteok` | Remote OK JSON API | MEDIUM (hiring signal) |
| `job_board:jobicy` | Jobicy JSON API | MEDIUM (hiring signal) |
| `news_rss` | Google News RSS search | EXPLICIT/STRONG only |
| `directory` | Association/directory pages | LEAD SIGNAL only |

## Active Sources

### WordPress Jobs (Primary)
- **URL**: `https://jobs.wordpress.net/feed/`
- **Format**: RSS 2.0 with `<dc:creator>` for company
- **Fetched**: 1 request per campaign
- **Quality**: Medium intent (hiring posts)

### We Work Remotely (Primary)
- **Feeds**:
  - `https://weworkremotely.com/categories/remote-programming-jobs.rss`
  - `https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss`
  - `https://weworkremotely.com/categories/remote-javascript-programming-jobs.rss`
  - `https://weworkremotely.com/categories/remote-python-programming-jobs.rss`
- **Format**: RSS 2.0 with `<dc:creator>` for company
- **Fetched**: 1 request per feed (4 feeds max)
- **Quality**: Medium intent (remote job posts)

### Remote OK (Primary)
- **URL**: `https://remoteok.com/api`
- **Format**: JSON array of job objects
- **Fetched**: 1 request per campaign
- **Quality**: Medium intent (remote job posts)
- **Note**: RSS feed discontinued (410 Gone); JSON API active

### Jobicy (Primary)
- **URL**: `https://jobicy.com/api/v2/remote-jobs?count=N`
- **Format**: JSON with `jobs` array
- **Fetched**: 1 request per campaign
- **Quality**: Medium intent (remote job posts)
- **Attribution**: Requires Jobicy credit with link

### Google News RSS (Secondary)
- **URL**: `https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en`
- **Format**: RSS 2.0 search results
- **Fetched**: 1 request per query
- **Quality**: Only EXPLICIT/STRONG intent phrases survive

### Directory/Association Pages (Tertiary)
- **Source**: User-provided URLs
- **Format**: HTML pages with company listings
- **Fetched**: 1 request per directory
- **Quality**: LEAD SIGNAL only (company existence, not intent)

## Deprecated/Unsupported Sources

| Source | Status | Reason |
|--------|--------|--------|
| Upwork RSS | Deprecated (2024) | Officially discontinued |
| Freelancer RSS | Unverified | Needs technical verification |
| Remote OK RSS | 410 Gone | Feed discontinued; use JSON API |

## Source Quality Tracking

BAM tracks per-source metrics in the `source_quality` table:
- `fetched`: Total items fetched
- `valid`: Items with valid structure
- `opportunities`: Items that became opportunities
- `qualified`: Items that passed qualification
- `contactable`: Items with extractable contacts
- `strong_intent`: Items with strong/explicit intent

Run `bam campaign-report` to see source quality stats.
