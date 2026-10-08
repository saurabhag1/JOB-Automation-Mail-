"""Pure text helpers: the tech vocabulary, experience/number parsing, email
classification, HTML-to-text and country detection. No network, no I/O, so
everything here is cheap to unit-test."""

from __future__ import annotations

import html as _html
import re
from functools import lru_cache

from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Tech vocabulary
# --------------------------------------------------------------------------- #
# (key, display name, weight, synonyms, narrower terms)
#
# Synonyms mean the same thing ("k8s" == Kubernetes). Narrower terms are
# specific kinds of the key ("RHEL" is a Linux). Both count when matching a
# job to the resume, but the truthfulness guard only lets a narrower term into
# tailored text if that exact term is already in the resume: knowing Linux does
# not make "RHEL" a fact.
_VOCAB = [
    # cloud platforms and services
    ("aws", "AWS", 2, ["aws", "amazon web services"], []),
    ("azure", "Azure", 2, ["azure", "microsoft azure"], []),
    ("gcp", "GCP", 2, ["gcp", "google cloud", "google cloud platform"], []),
    ("oci", "Oracle Cloud", 1, ["oci", "oracle cloud", "oracle cloud infrastructure"], []),
    ("ec2", "EC2", 1, ["ec2", "amazon ec2"], []),
    ("s3", "S3", 1, ["s3", "amazon s3"], []),
    ("rds", "RDS", 1, ["rds", "amazon rds"], ["aurora"]),
    ("lambda", "Lambda", 1, ["lambda", "aws lambda"], []),
    ("ecs", "ECS", 1, ["ecs", "amazon ecs", "elastic container service"], []),
    ("fargate", "Fargate", 1, ["fargate"], []),
    ("eks", "EKS", 1.5, ["eks", "amazon eks", "elastic kubernetes service"], []),
    ("aks", "AKS", 1, ["aks", "azure kubernetes service"], []),
    ("gke", "GKE", 1, ["gke", "google kubernetes engine"], []),
    ("cloudwatch", "CloudWatch", 1, ["cloudwatch", "cloud watch"], []),
    ("iam", "IAM", 1, ["iam", "identity and access management"], []),
    ("vpc", "VPC", 1, ["vpc", "vpcs", "virtual private cloud"], []),
    ("elb", "Load Balancers", 1, ["elb", "alb", "nlb", "elastic load balancer", "elastic load balancing",
                                  "load balancer", "load balancers", "load balancing"], []),
    ("route53", "Route 53", 1, ["route 53", "route53"], []),
    ("cloudfront", "CloudFront", 1, ["cloudfront"], []),
    ("dynamodb", "DynamoDB", 1, ["dynamodb"], []),
    ("sqs", "SQS", 1, ["sqs"], []),
    ("sns", "SNS", 1, ["sns"], []),
    ("api_gateway", "API Gateway", 1, ["api gateway"], []),
    ("beanstalk", "Elastic Beanstalk", 1, ["elastic beanstalk", "beanstalk"], []),
    ("secrets_manager", "Secrets Manager", 1, ["secrets manager", "aws secrets manager"], []),
    ("kms", "KMS", 1, ["kms"], []),
    ("cost_explorer", "AWS Cost Explorer", 1, ["cost explorer"], []),
    ("cloudformation", "CloudFormation", 1.5, ["cloudformation", "cloud formation", "cfn"], []),
    ("aws_cdk", "AWS CDK", 1, ["aws cdk", "cdk"], []),
    ("sagemaker", "SageMaker", 1, ["sagemaker", "sage maker"], []),
    ("bedrock", "Bedrock", 1, ["bedrock", "amazon bedrock"], []),
    ("serverless", "Serverless", 1, ["serverless"], []),
    ("multicloud", "Multi-cloud", 1, ["multi cloud", "multicloud"], []),
    ("cloudflare", "Cloudflare", 1, ["cloudflare"], []),
    ("cdn", "CDN", 1, ["cdn", "cdns"], []),
    # containers and orchestration
    ("docker", "Docker", 2, ["docker", "dockerfile", "dockerfiles", "docker compose"], []),
    ("kubernetes", "Kubernetes", 2, ["kubernetes", "k8s"], []),
    ("helm", "Helm", 1.5, ["helm", "helm chart", "helm charts"], []),
    ("openshift", "OpenShift", 1, ["openshift"], []),
    ("istio", "Istio", 1, ["istio"], []),
    ("linkerd", "Linkerd", 1, ["linkerd"], []),
    ("service_mesh", "Service Mesh", 1, ["service mesh"], []),
    ("karpenter", "Karpenter", 1, ["karpenter"], []),
    ("hpa", "HPA", 1, ["hpa", "horizontal pod autoscaler", "horizontal pod autoscaling"], []),
    ("autoscaling", "Auto Scaling", 1, ["auto scaling", "autoscaling"], []),
    ("containers", "Containerization", 1, ["containerization", "containerisation", "containerized",
                                           "containerised", "containers"], []),
    ("microservices", "Microservices", 1, ["microservices", "microservice", "micro services"], []),
    ("kustomize", "Kustomize", 1, ["kustomize"], []),
    ("rancher", "Rancher", 1, ["rancher"], []),
    # infrastructure as code and configuration management
    ("terraform", "Terraform", 2, ["terraform"], []),
    ("terragrunt", "Terragrunt", 1, ["terragrunt"], []),
    ("pulumi", "Pulumi", 1, ["pulumi"], []),
    ("ansible", "Ansible", 2, ["ansible"], []),
    ("chef", "Chef", 1, ["chef"], []),
    ("puppet", "Puppet", 1, ["puppet"], []),
    ("saltstack", "SaltStack", 1, ["saltstack"], []),
    ("packer", "Packer", 1, ["packer"], []),
    ("iac", "IaC", 1.5, ["iac", "infrastructure as code"], []),
    ("crossplane", "Crossplane", 1, ["crossplane"], []),
    ("vagrant", "Vagrant", 1, ["vagrant"], []),
    # CI/CD and release
    ("cicd", "CI/CD", 2, ["ci/cd", "ci cd", "cicd", "continuous integration", "continuous delivery",
                          "continuous deployment"], []),
    ("jenkins", "Jenkins", 2, ["jenkins"], []),
    ("github_actions", "GitHub Actions", 1.5, ["github actions"], []),
    ("gitlab_ci", "GitLab CI/CD", 1.5, ["gitlab ci", "gitlab ci/cd", "gitlab pipelines"], []),
    ("argocd", "Argo CD", 1.5, ["argocd", "argo cd"], ["argo workflows", "argo rollouts"]),
    ("fluxcd", "Flux CD", 1, ["fluxcd", "flux cd"], []),
    ("gitops", "GitOps", 1, ["gitops"], []),
    ("azure_devops", "Azure DevOps", 1, ["azure devops", "azure pipelines"], []),
    ("circleci", "CircleCI", 1, ["circleci", "circle ci"], []),
    ("travis", "Travis CI", 1, ["travis ci", "travisci"], []),
    ("teamcity", "TeamCity", 1, ["teamcity"], []),
    ("bamboo", "Bamboo", 1, ["bamboo"], []),
    ("spinnaker", "Spinnaker", 1, ["spinnaker"], []),
    ("tekton", "Tekton", 1, ["tekton"], []),
    ("artifactory", "Artifactory", 1, ["artifactory", "jfrog"], []),
    ("nexus", "Nexus", 1, ["nexus"], []),
    ("maven", "Maven", 1, ["maven"], []),
    ("gradle", "Gradle", 1, ["gradle"], []),
    ("sonarqube", "SonarQube", 1, ["sonarqube", "sonar", "sonarcloud"], []),
    ("trivy", "Trivy", 1, ["trivy"], []),
    ("snyk", "Snyk", 1, ["snyk"], []),
    ("owasp", "OWASP", 1, ["owasp"], []),
    ("blue_green", "Blue-Green Deployments", 1, ["blue green", "blue/green"], []),
    ("canary", "Canary Releases", 1, ["canary"], []),
    ("rollback", "Rollbacks", 0.5, ["rollback", "rollbacks"], []),
    # observability and reliability
    ("prometheus", "Prometheus", 1.5, ["prometheus"], []),
    ("grafana", "Grafana", 1.5, ["grafana"], []),
    ("elk", "ELK Stack", 1, ["elk", "elk stack"], ["elasticsearch", "logstash", "kibana"]),
    ("opensearch", "OpenSearch", 1, ["opensearch"], []),
    ("loki", "Loki", 1, ["loki"], []),
    ("tempo", "Tempo", 1, ["grafana tempo", "tempo"], []),
    ("mimir", "Mimir", 1, ["mimir"], []),
    ("jaeger", "Jaeger", 1, ["jaeger"], []),
    ("opentelemetry", "OpenTelemetry", 1, ["opentelemetry", "otel"], []),
    ("datadog", "Datadog", 1, ["datadog"], []),
    ("newrelic", "New Relic", 1, ["new relic", "newrelic"], []),
    ("splunk", "Splunk", 1, ["splunk"], []),
    ("dynatrace", "Dynatrace", 1, ["dynatrace"], []),
    ("appdynamics", "AppDynamics", 1, ["appdynamics"], []),
    ("nagios", "Nagios", 1, ["nagios"], []),
    ("zabbix", "Zabbix", 1, ["zabbix"], []),
    ("pagerduty", "PagerDuty", 1, ["pagerduty"], []),
    ("observability", "Observability", 1, ["observability"], []),
    ("monitoring", "Monitoring", 0.5, ["monitoring"], []),
    ("logging", "Logging", 0.5, ["logging", "log management", "log analysis", "centralized logging"], []),
    ("alerting", "Alerting", 0.5, ["alerting"], []),
    ("tracing", "Distributed Tracing", 1, ["distributed tracing", "tracing"], []),
    ("mttr", "MTTR", 0.5, ["mttr"], []),
    ("slo", "SLOs/SLIs", 1, ["slo", "slos", "sli", "slis", "error budget", "error budgets"], []),
    ("sla", "SLAs", 0.5, ["sla", "slas"], []),
    ("incident", "Incident Management", 1, ["incident management", "incident response", "on call",
                                            "oncall", "postmortem", "postmortems", "post mortem",
                                            "root cause analysis", "rca"], []),
    ("chaos", "Chaos Engineering", 1, ["chaos engineering"], []),
    ("sre", "SRE", 1.5, ["sre", "site reliability", "site reliability engineering"], []),
    ("high_availability", "High Availability", 1, ["high availability", "highly available",
                                                   "fault tolerance", "fault tolerant"], []),
    ("disaster_recovery", "Disaster Recovery", 1, ["disaster recovery", "backup and recovery",
                                                   "business continuity"], []),
    ("performance", "Performance Tuning", 0.5, ["performance tuning", "performance optimization",
                                                "capacity planning"], []),
    # security
    ("devsecops", "DevSecOps", 1.5, ["devsecops", "dev sec ops"], []),
    ("vault", "HashiCorp Vault", 1, ["vault", "hashicorp vault"], []),
    ("secrets", "Secrets Management", 1, ["secrets management", "secret management", "secrets"], []),
    ("compliance", "Security Compliance", 0.5, ["compliance", "security compliance"],
     ["soc 2", "soc2", "iso 27001", "pci dss", "hipaa", "gdpr"]),
    ("sast", "SAST/DAST", 1, ["sast", "dast"], []),
    ("waf", "WAF", 1, ["waf"], []),
    ("guardduty", "GuardDuty", 1, ["guardduty"], []),
    ("ssl", "SSL/TLS", 1, ["ssl", "tls", "https", "ssl/tls"], []),
    # languages and tooling
    ("python", "Python", 1.5, ["python"], []),
    ("bash", "Bash/Shell", 1.5, ["bash", "shell", "shell scripting", "bash scripting", "shell scripts"], []),
    ("powershell", "PowerShell", 1, ["powershell"], []),
    ("golang", "Go", 1, ["golang", "go lang", "go language"], []),
    ("java", "Java", 1, ["java"], []),
    ("javascript", "JavaScript", 1, ["javascript"], []),
    ("typescript", "TypeScript", 1, ["typescript"], []),
    ("nodejs", "Node.js", 1, ["node.js", "nodejs", "node js"], []),
    ("groovy", "Groovy", 1, ["groovy"], []),
    ("yaml", "YAML", 0.5, ["yaml"], []),
    ("sql", "SQL", 1, ["sql"], []),
    ("ruby", "Ruby", 1, ["ruby"], []),
    ("rust", "Rust", 1, ["rust"], []),
    ("mern", "MERN Stack", 1, ["mern"], []),
    ("rest_api", "REST APIs", 1, ["rest api", "rest apis", "restful"], []),
    ("git", "Git", 1, ["git"], []),
    ("github", "GitHub", 1, ["github"], []),
    ("gitlab", "GitLab", 1, ["gitlab"], []),
    ("bitbucket", "Bitbucket", 1, ["bitbucket"], []),
    ("jira", "Jira", 0.5, ["jira"], []),
    # operating systems and networking
    ("linux", "Linux", 2, ["linux", "unix"], ["ubuntu", "rhel", "red hat", "redhat", "centos", "debian",
                                              "amazon linux"]),
    ("windows_server", "Windows Server", 1, ["windows server"], []),
    ("networking", "Networking", 1, ["networking", "tcp/ip"], []),
    ("dns", "DNS", 1, ["dns"], []),
    ("vpn", "VPN", 1, ["vpn", "vpns"], []),
    ("firewall", "Firewalls", 1, ["firewall", "firewalls"], []),
    ("nginx", "NGINX", 1, ["nginx"], []),
    ("haproxy", "HAProxy", 1, ["haproxy"], []),
    ("apache", "Apache HTTP Server", 1, ["apache http server", "httpd"], []),
    # data and messaging
    ("kafka", "Kafka", 1, ["kafka"], []),
    ("rabbitmq", "RabbitMQ", 1, ["rabbitmq"], []),
    ("redis", "Redis", 1, ["redis"], []),
    ("postgresql", "PostgreSQL", 1, ["postgresql", "postgres"], []),
    ("mysql", "MySQL", 1, ["mysql"], []),
    ("mongodb", "MongoDB", 1, ["mongodb", "mongo"], []),
    ("spark", "Spark", 1, ["spark", "pyspark"], []),
    ("airflow", "Airflow", 1, ["airflow"], []),
    ("databricks", "Databricks", 1, ["databricks"], []),
    ("snowflake", "Snowflake", 1, ["snowflake"], []),
    ("hadoop", "Hadoop", 1, ["hadoop"], []),
    # ML and AI
    ("mlops", "MLOps", 1.5, ["mlops", "ml ops"], []),
    ("mlflow", "MLflow", 1, ["mlflow"], []),
    ("kubeflow", "Kubeflow", 1, ["kubeflow"], []),
    ("ml", "Machine Learning", 1, ["machine learning", "ml", "ai/ml", "aiml"], []),
    ("ai", "AI", 1, ["ai", "artificial intelligence", "genai", "gen ai", "generative ai", "llm", "llms",
                     "agentic ai"], []),
    ("gpu", "GPU", 1, ["gpu", "gpus", "cuda"], []),
    # certifications - listed so the guard can stop a rewrite from claiming one
    ("cka", "CKA", 1, ["cka", "certified kubernetes administrator"], []),
    ("ckad", "CKAD", 1, ["ckad", "certified kubernetes application developer"], []),
    ("cks", "CKS", 1, ["cks", "certified kubernetes security specialist"], []),
    ("aws_saa", "AWS Solutions Architect", 1, ["aws certified solutions architect", "solutions architect associate",
                                               "solutions architect professional", "saa c03", "aws saa"], []),
    ("aws_devops_pro", "AWS DevOps Engineer Professional", 1, ["devops engineer professional", "dop c02"], []),
    ("aws_sysops", "AWS SysOps Administrator", 1, ["sysops administrator", "soa c02"], []),
    ("aws_ml_cert", "AWS Certified Machine Learning", 1, ["aws certified machine learning"], []),
    ("azure_certs", "Azure certification", 1, ["az 104", "az 400", "az 305", "az 900", "azure administrator associate",
                                                "azure devops engineer expert"], []),
    ("gcp_certs", "Google Cloud certification", 1, ["associate cloud engineer", "professional cloud architect",
                                                    "professional cloud devops engineer"], []),
    ("terraform_cert", "Terraform Associate", 1, ["terraform associate", "hashicorp certified"], []),
    ("rhce", "Red Hat certification", 1, ["rhce", "rhcsa"], []),
    # practices
    ("agile", "Agile", 0.5, ["agile", "scrum", "kanban"], []),
    ("finops", "FinOps", 1, ["finops", "cost optimization", "cost optimisation", "cloud cost"], []),
    ("automation", "Automation", 0.5, ["automation", "automate", "automated", "automating"], []),
]

# A posting must mention at least one of these to count as a DevOps/Cloud role.
CORE_TERMS = {
    "aws", "azure", "gcp", "kubernetes", "docker", "terraform", "ansible", "cicd", "jenkins",
    "github_actions", "gitlab_ci", "argocd", "eks", "aks", "gke", "sre", "devsecops",
    "cloudformation", "helm", "linux", "mlops", "iac", "openshift", "azure_devops",
}

DISPLAY = {key: display for key, display, *_ in _VOCAB}
WEIGHT = {key: weight for key, _, weight, *_ in _VOCAB}

# Using a child is using its parents: EKS work is AWS work and Kubernetes work.
# Never the other way round - knowing AWS says nothing about EKS.
IMPLIES = {
    "eks": ["aws", "kubernetes"], "ecs": ["aws", "containers"], "fargate": ["aws", "serverless"],
    "ec2": ["aws"], "s3": ["aws"], "rds": ["aws"], "lambda": ["aws", "serverless"], "dynamodb": ["aws"],
    "cloudwatch": ["aws", "monitoring"], "route53": ["aws"], "cloudfront": ["aws", "cdn"], "sqs": ["aws"],
    "sns": ["aws"], "api_gateway": ["aws"], "beanstalk": ["aws"], "secrets_manager": ["aws", "secrets"],
    "kms": ["aws"], "cost_explorer": ["aws", "finops"], "cloudformation": ["aws", "iac"],
    "aws_cdk": ["aws", "iac"], "sagemaker": ["aws", "ml", "mlops"], "bedrock": ["aws", "ai"],
    "guardduty": ["aws"], "karpenter": ["kubernetes", "autoscaling"], "aks": ["azure", "kubernetes"],
    "gke": ["gcp", "kubernetes"], "openshift": ["kubernetes"], "helm": ["kubernetes"],
    "hpa": ["kubernetes", "autoscaling"], "kustomize": ["kubernetes"], "istio": ["service_mesh", "kubernetes"],
    "linkerd": ["service_mesh", "kubernetes"], "docker": ["containers"], "kubernetes": ["containers"],
    "terraform": ["iac"], "terragrunt": ["terraform", "iac"], "pulumi": ["iac"],
    "jenkins": ["cicd"], "github_actions": ["cicd", "github"], "gitlab_ci": ["cicd", "gitlab"],
    "argocd": ["gitops", "cicd", "kubernetes"], "fluxcd": ["gitops", "kubernetes"], "azure_devops": ["cicd", "azure"],
    "circleci": ["cicd"], "travis": ["cicd"], "teamcity": ["cicd"], "bamboo": ["cicd"], "spinnaker": ["cicd"],
    "tekton": ["cicd", "kubernetes"], "prometheus": ["monitoring", "alerting"], "grafana": ["monitoring", "observability"],
    "elk": ["logging"], "opensearch": ["logging"], "loki": ["logging"], "tempo": ["tracing"], "jaeger": ["tracing"],
    "opentelemetry": ["observability", "tracing"], "mimir": ["monitoring"], "datadog": ["monitoring", "observability"],
    "newrelic": ["monitoring"], "splunk": ["logging"], "dynatrace": ["monitoring"], "vault": ["secrets"],
    "sonarqube": ["devsecops"], "trivy": ["devsecops"], "snyk": ["devsecops"], "mlflow": ["mlops"],
    "kubeflow": ["mlops", "kubernetes"], "blue_green": ["cicd"], "canary": ["cicd"], "rollback": ["cicd"],
}


def implied(keys) -> set[str]:
    """*keys* plus everything they imply (transitively)."""
    out = set(keys)
    frontier = list(out)
    while frontier:
        for parent in IMPLIES.get(frontier.pop(), []):
            if parent not in out:
                out.add(parent)
                frontier.append(parent)
    return out


def _alias_pattern(alias: str) -> str:
    # "auto scaling" also matches "auto-scaling"; "blue green" matches "blue-green".
    return r"[\s\-]+".join(re.escape(part) for part in alias.split())


def _compile(aliases: list[str]) -> re.Pattern | None:
    if not aliases:
        return None
    body = "|".join(_alias_pattern(a) for a in sorted(aliases, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])")


_PATTERNS = [(key, _compile(syn), _compile(narrow)) for key, _, _, syn, narrow in _VOCAB]


def find_terms(text: str) -> dict[str, int]:
    """Vocabulary keys mentioned in *text* -> number of mentions."""
    low = (text or "").lower()
    found: dict[str, int] = {}
    for key, syn, narrow in _PATTERNS:
        hits = len(syn.findall(low)) if syn else 0
        if narrow:
            hits += len(narrow.findall(low))
        if hits:
            found[key] = hits
    return found


def unsupported_terms(candidate: str, source: str) -> list[str]:
    """Tech terms in *candidate* that *source* does not back up.

    A synonym is backed by any mention of its key in the source; a narrower
    term only by that same narrower term.
    """
    cand, src = (candidate or "").lower(), (source or "").lower()
    src_keys = implied(find_terms(src))
    problems = []
    for key, syn, narrow in _PATTERNS:
        if syn and syn.search(cand) and key not in src_keys:
            problems.append(DISPLAY[key])
        if narrow:
            for m in narrow.finditer(cand):
                if not _compile([m.group(0)]).search(src):
                    problems.append(m.group(0))
    return sorted(set(problems))


def display(keys) -> list[str]:
    return [DISPLAY.get(k, k) for k in keys]


def highlight_html(text: str, keys) -> str:
    """HTML-escape *text*, wrapping mentions of the given vocabulary keys in
    <strong> (used to bold the job's must-haves inside resume bullets)."""
    keys = set(keys or ())
    low = (text or "").lower()
    if not keys or len(low) != len(text or ""):
        return _html.escape(text or "")
    spans = []
    for key, syn, narrow in _PATTERNS:
        if key in keys:
            for rx in (syn, narrow):
                if rx:
                    spans += [(m.start(), m.end()) for m in rx.finditer(low)]
    spans.sort(key=lambda s: (s[0], s[0] - s[1]))
    out, pos = [], 0
    for start, end in spans:
        if start < pos:
            continue  # overlaps a longer match already bolded
        out.append(_html.escape(text[pos:start]))
        out.append(f'<strong class="k">{_html.escape(text[start:end])}</strong>')
        pos = end
    out.append(_html.escape(text[pos:]))
    return "".join(out)


# --------------------------------------------------------------------------- #
# Numbers (for the truthfulness guard)
# --------------------------------------------------------------------------- #
_NUMBER = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*(%|percent\b|\+)?")


def numbers_in(text: str) -> set[str]:
    """Metric-like numbers: '40%', '99.9%', '20+', '3'. Ignores digits inside
    names such as EC2 or S3."""
    out = set()
    for value, unit in _NUMBER.findall(text or ""):
        unit = "%" if unit and unit.startswith("p") else (unit or "")
        out.add(value + unit)
    return out


# --------------------------------------------------------------------------- #
# Experience
# --------------------------------------------------------------------------- #
_YRS = r"(?:years?|yrs?)"
_EXP_PATTERNS = [
    # 3-5 years, 3 to 5 yrs, 3+ - 5 years
    ("range", re.compile(rf"(\d{{1,2}})(?:\.\d)?\s*\+?\s*(?:-|–|—|to)\s*(\d{{1,2}})(?:\.\d)?\s*\+?\s*{_YRS}", re.I)),
    # minimum 3 years, at least 3 yrs
    ("min", re.compile(rf"(?:minimum|min\.?|at\s*least|atleast)\s*(?:of\s*)?(\d{{1,2}})(?:\.\d)?\+?\s*{_YRS}", re.I)),
    # 3+ years
    ("plus", re.compile(rf"(\d{{1,2}})(?:\.\d)?\s*\+\s*{_YRS}", re.I)),
    # 3 years
    ("single", re.compile(rf"(\d{{1,2}})(?:\.\d)?\s*{_YRS}", re.I)),
]
_EXP_WORD = re.compile(r"experience|\bexp\b|experienced", re.I)


def parse_experience(text: str, explicit: bool = False) -> tuple[int | None, int | None]:
    """(min, max) years a posting asks for; max may be None ('3+').

    Descriptions mention years in other contexts ("founded 25 years ago"), so
    unless the text is an explicit experience field (Naukri's '6-11 Yrs'), a
    match only counts if it sits within ~100 characters of "experience".
    """
    text = text or ""
    anchors = [m.start() for m in _EXP_WORD.finditer(text)]
    best = None
    for kind, rx in _EXP_PATTERNS:
        for m in rx.finditer(text):
            dist = min((abs(m.start() - a) for a in anchors), default=10**6)
            if not explicit and dist > 100:
                continue
            lo = int(m.group(1))
            hi = int(m.group(2)) if kind == "range" else (None if kind in ("plus", "min") else lo)
            if lo > 25 or (hi is not None and hi > 30):
                continue
            if hi is not None and hi < lo:
                lo, hi = hi, lo
            rank = (dist, m.start())
            if best is None or rank < best[0]:
                best = (rank, lo, hi)
        if best:
            break  # the first pattern kind with a hit wins (range beats single)
    return (best[1], best[2]) if best else (None, None)


# --------------------------------------------------------------------------- #
# Emails
# --------------------------------------------------------------------------- #
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

_REJECT_LOCAL = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|mailer-?daemon|postmaster|automated|notifications?|"
    r"bounce|webmaster|abuse|support|accommodations?|accessibility|ada|eeo|complaints?|"
    r"grievances?|dpo|newsletter|marketing|billing|accounts?|payroll|vendor|finance|privacy|"
    r"legal|press|media|investor|unsubscribe|compliance|security)|.*(fraud|scam|phishing|spam)",
    re.I,
)
_GENERIC_LOCAL = re.compile(
    r"^(hr|hrd|hiring|career|careers|job|jobs|recruit|recruiter|recruiting|recruitment|talent|"
    r"talents|resume|cv|apply|application|info|contact|team|office|admin|enquiry|hello)(?=[._\-0-9]|$)",
    re.I,
)
_PLACEHOLDER_DOMAINS = {"example.com", "email.com", "domain.com", "yourcompany.com", "company.com"}
FREE_MAIL = {"gmail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com", "rediffmail.com",
             "live.com", "icloud.com", "protonmail.com", "aol.com"}


def classify_email(email: str) -> str:
    """'personal' (a named person), 'generic' (hr@, careers@) or 'reject'."""
    email = (email or "").strip().lower()
    local, _, domain = email.partition("@")
    if not local or "." not in domain or ".." in email or "…" in email or len(local) < 2:
        return "reject"
    if _REJECT_LOCAL.match(local) or domain in _PLACEHOLDER_DOMAINS:
        return "reject"
    if domain.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
        return "reject"
    return "generic" if _GENERIC_LOCAL.match(local) else "personal"


# Words around an address that mark it as an application inbox...
_APPLY_INTENT = re.compile(
    r"send (?:your|ur|me|in)|share (?:your|ur|their|the|updated)|drop (?:your|ur|a)|mail (?:your|ur|me)|"
    r"e-?mail (?:your|ur|me|us)|forward (?:your|ur)|interested (?:candidates|applicants|folks|people)|"
    r"apply (?:at|via|by|to|on)|reach (?:out|me)|resume|\bcvs?\b|curriculum vitae|hiring|referr", re.I)
# ...and words that mark it as anything but (accommodation, privacy, fraud notices).
_NOT_FOR_APPLYING = re.compile(
    r"accommodat|disabilit|privacy|data protection|gdpr|fraud|scam|phishing|equal (?:employment )?opportunit|"
    r"\beeo\b|unsolicited|do not (?:send|email|apply)|agenc(?:y|ies)|third[- ]party|accessib|grievance|"
    r"complain|whistle|investor|press|media inquir", re.I)


_SENTENCE_END = re.compile(r"[.!?](?=\s)|\n")


def pick_emails(text: str, context_check: bool = True) -> tuple[str, list[str]]:
    """(best address to send an application to, other usable addresses).

    Job descriptions mention many inboxes - accessibility requests, privacy
    officers, fraud warnings. With *context_check*, an address only counts if
    the text around it does not mark it as one of those, and a generic inbox
    (talent@, hr@) additionally needs application wording nearby ("share your
    CV at ..."). Personal addresses beat generic ones.
    """
    personal, generic = [], []
    text = text or ""
    for m in EMAIL_RE.finditer(text):
        email = m.group(0).strip(".").lower()
        kind = classify_email(email)
        if kind == "reject" or email in personal or email in generic:
            continue
        if context_check:
            # Warnings only count inside the address's own sentence ("For accommodation
            # requests contact x@"); the next sentence may be about something else.
            before = text[: m.start()]
            starts = [b.end() for b in _SENTENCE_END.finditer(before)]
            start = starts[-1] if starts else 0
            after = _SENTENCE_END.search(text, m.end())
            sentence = text[start: after.start() if after else len(text)]
            if _NOT_FOR_APPLYING.search(sentence):
                continue
            if kind == "generic" and not _APPLY_INTENT.search(text[max(0, m.start() - 220): m.start()] + sentence):
                continue
        (personal if kind == "personal" else generic).append(email)
    ordered = personal + generic
    return (ordered[0], ordered[1:]) if ordered else ("", [])


# --------------------------------------------------------------------------- #
# HTML and whitespace
# --------------------------------------------------------------------------- #
def html_to_text(markup: str) -> str:
    if not markup:
        return ""
    if "<" not in markup and "&" not in markup:
        return markup.strip()
    soup = BeautifulSoup(_html.unescape(markup), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    text = soup.get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# --------------------------------------------------------------------------- #
# Countries
# --------------------------------------------------------------------------- #
_COUNTRY_WORDS = [
    ("India", ["india", "pune", "bengaluru", "bangalore", "hyderabad", "mumbai", "navi mumbai", "thane",
               "noida", "gurugram", "gurgaon", "delhi", "new delhi", "chennai", "kolkata", "ahmedabad",
               "nagpur", "indore", "jaipur", "kochi", "coimbatore", "chandigarh", "trivandrum",
               "maharashtra", "karnataka", "telangana", "tamil nadu", "haryana", "uttar pradesh",
               "gujarat", "kerala", "west bengal", "delhi ncr", "ncr"]),
    ("Singapore", ["singapore"]),
    ("UAE", ["united arab emirates", "uae", "dubai", "abu dhabi", "sharjah"]),
    ("Japan", ["japan", "tokyo", "osaka", "yokohama"]),
    ("Netherlands", ["netherlands", "amsterdam", "rotterdam", "utrecht", "eindhoven"]),
    ("Germany", ["germany", "berlin", "munich", "münchen", "frankfurt", "hamburg", "cologne"]),
    ("United Kingdom", ["united kingdom", "uk", "england", "london", "manchester", "edinburgh", "scotland"]),
    ("Ireland", ["ireland", "dublin"]),
    ("Canada", ["canada", "toronto", "vancouver", "montreal", "ottawa"]),
    ("Australia", ["australia", "sydney", "melbourne", "brisbane"]),
    ("United States", ["united states", "usa", "u.s.", "u.s.a", "us", "new york", "san francisco",
                       "seattle", "austin", "boston", "chicago", "los angeles", "atlanta", "denver",
                       "california", "texas", "washington", "virginia", "new jersey", "florida",
                       "massachusetts", "north carolina", "georgia", "colorado", "illinois"]),
    ("Europe", ["europe", "eu", "emea", "european union"]),
    ("LATAM", ["latam", "latin america", "south america"]),
    ("APAC", ["apac", "asia pacific", "asia"]),
    ("Worldwide", ["worldwide", "anywhere", "global", "globally"]),
]
# Any other country is named as itself: a remote role "based in Portugal" is a
# Portugal-only role, not a worldwide one.
_OTHER_COUNTRIES = [
    "Portugal", "Spain", "France", "Italy", "Poland", "Hungary", "Romania", "Czech Republic", "Czechia",
    "Slovakia", "Austria", "Switzerland", "Belgium", "Luxembourg", "Sweden", "Norway", "Denmark", "Finland",
    "Estonia", "Latvia", "Lithuania", "Greece", "Croatia", "Serbia", "Bulgaria", "Ukraine", "Turkey", "Cyprus",
    "Malta", "Israel", "Egypt", "Morocco", "South Africa", "Nigeria", "Kenya", "Saudi Arabia", "Qatar", "Bahrain",
    "Kuwait", "Oman", "Pakistan", "Bangladesh", "Sri Lanka", "Nepal", "China", "Hong Kong", "Taiwan",
    "South Korea", "Korea", "Vietnam", "Philippines", "Malaysia", "Indonesia", "Thailand", "New Zealand",
    "Brazil", "Mexico", "Argentina", "Colombia", "Chile", "Peru", "Uruguay", "Costa Rica",
]
_COUNTRY_WORDS += [(name, [name.lower()]) for name in _OTHER_COUNTRIES]
_COUNTRY_WORDS += [("Vietnam", ["ho chi minh", "hanoi"]), ("Portugal", ["lisbon", "porto"]),
                   ("Spain", ["madrid", "barcelona"]), ("France", ["paris"]), ("Poland", ["warsaw", "krakow"]),
                   ("Philippines", ["manila"]), ("Malaysia", ["kuala lumpur"]), ("Chile", ["santiago"]),
                   ("Brazil", ["são paulo", "sao paulo"]), ("Mexico", ["mexico city"]),
                   ("Hungary", ["budapest"]), ("Romania", ["bucharest"])]
_COUNTRY_RX = [(country, _compile(words)) for country, words in _COUNTRY_WORDS]


@lru_cache(maxsize=4096)
def detect_country(location: str) -> str:
    """Best-effort country for a location string; '' when unknown."""
    found = detect_countries(location)
    return found[0] if found else ""


@lru_cache(maxsize=4096)
def detect_countries(location: str) -> tuple[str, ...]:
    """Every country/region a location string names: 'EMEA, LATAM, Canada, USA'
    -> ('Canada', 'United States', 'Europe', 'LATAM')."""
    low = (location or "").lower()
    return tuple(dict.fromkeys(country for country, rx in _COUNTRY_RX if rx.search(low)))
