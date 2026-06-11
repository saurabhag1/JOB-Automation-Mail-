"""Central configuration for the Job Lead Analyzer.

Edit the values here to change what the tool searches for. Everything that a
user would reasonably want to tweak (skills, target roles, locations, experience
range, how many leads per run) lives in this file so the rest of the code does
not need to be touched.
"""

# --------------------------------------------------------------------------- #
# Candidate skills (yours). A job is scored by how many of ITS skills you have.
# Taken from the existing sample output so the match logic mirrors it.
# --------------------------------------------------------------------------- #
CANDIDATE_SKILLS = [
    "devops",
    "jenkins",
    "github actions",
    "gitlab ci",
    "docker",
    "kubernetes",
    "helm",
    "terraform",
    "ansible",
    "aws",
    "azure",
    "gcp",
    "linux",
    "python",
    "bash scripting",
    "shell scripting",
    "monitoring",
    "prometheus",
    "grafana",
    "elk stack",
    "microservices",
    "devsecops",
    "infrastructure as code",
    "iac",
    "containerization",
    "git",
    "sonarqube",
    "sre",
    "cloudformation",
    "argocd",
    "vpc",
    "bedrock",
    "sagemaker",
    "machine learning",
    "observability",
    "ai",
    "ml",
    "automation",
    "playwrite",
    
]

# Roles we are hunting for. Used to build search queries and to label the lead.
ROLE_KEYWORDS = [
    "devops engineer",
    "aws engineer",
    "aws devops engineer",
    "cloud engineer",
    "site reliability engineer",
    "platform engineer",
    "azure devops engineer",
]

# Locations, ordered by your preference. Earlier = higher rank.
# The filter is permissive (a job in any location can pass); this list is used
# to TAG and RANK leads, with remote / work-from-home prioritized first.
LOCATION_PRIORITY = [
    "remote",
    "work from home",
    "wfh",
    "pune",
    "mumbai",
    "maharashtra",
    "nagpur",
    "bangalore",
    "bengaluru",
    "hyderabad",
    "delhi",
    "noida",
    "gurgaon",
    "gurugram",
    "chennai",
    "india",
    "singapore",
    "dubai",
    "uae",
    "abu dhabi",
    "vietnam",
    "ho chi minh",
    "hanoi",
    "usa",
    "united states",
    "uk",
    "united kingdom",
    "london",
    "germany",
    "netherlands",
    "australia",
    "canada",
]

# Locations used to seed search queries (kept short to conserve SerpApi credits).
SEARCH_LOCATIONS = ["remote", "pune", "mumbai", "bangalore", "india", "singapore", "uae", "vietnam"]

# Job portals to target in search (people post genuine "share your resume at X"
# hiring posts on these). LinkedIn posts are by far the richest source of real
# personal HR emails; the structured portals are included for coverage.
PORTALS = [
    "linkedin.com",
    "naukri.com",
    "indeed.com",
    "glassdoor.com",
    "instahyre.com",
    "hirect.in",
    "cutshort.io",
    "wellfound.com",
]

# Recency: only keep posts from the last N days (override with --days).
RECENCY_DAYS = 4
# SerpApi time filter: qdr:d=24h, qdr:w=week, qdr:m=month. Week ≈ "last few days".
SERPAPI_RECENCY = "qdr:w"

# Experience window you want (inclusive). A job qualifies if ITS range overlaps
# this window. Override per-run with --exp-min / --exp-max.
EXPERIENCE_MIN = 0
EXPERIENCE_MAX = 5

# Relevance gate: a post must mention at least one CORE skill (so it's really a
# DevOps/Cloud role) and at least MIN_SKILL_MATCHES of your skills total. This
# is what "match my skill set, leave the unwanted jobs" enforces.
CORE_SKILLS = [
    "devops", "aws", "azure", "gcp", "kubernetes", "terraform", "ansible",
    "docker", "jenkins", "cloudformation", "sre", "argocd", "devsecops",
]
MIN_SKILL_MATCHES = 2

# SERP providers to scrape, in order. Each returns Google organic results and
# is queried independently, so each contributes DIFFERENT jobs/HR emails. A
# provider whose API key (see name -> env var in serp_providers.PROVIDERS) is
# missing or that errors out is skipped -- the rest still run (graceful
# fallback). Free tiers: serpapi 250/mo, serper 2,500, scraperapi 5,000,
# scrapingdog 1,000, searchapi 100.
SERP_PROVIDERS = ["serpapi", "serper", "scraperapi", "scrapingdog", "searchapi"]

# How many fresh, unique leads to keep PER PROVIDER (override --per-provider).
# 5 providers x 5 = up to 25 unique leads per run.
PER_PROVIDER = 5

# How many fresh, unique leads to collect per run total. Override --target-count.
# Defaults to PER_PROVIDER x number of SERP providers (5 x 5 = 25).
TARGET_COUNT = PER_PROVIDER * len(SERP_PROVIDERS)

# Default search budget per provider per run (each = 1 search credit; Scrapingdog
# charges 5). Keep low for daily use to conserve the free tiers.
MAX_SEARCHES = 8

# Networking
REQUEST_TIMEOUT = 15  # seconds
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Output file names (written into the current working directory).
OUTPUT_JSON = "daily_job_leads.json"
STATE_FILE = "state.json"
