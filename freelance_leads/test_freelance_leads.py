"""python job_autopilot/.venv/bin/python -m pytest freelance_leads -q"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_autopilot"))

import freelance_leads as fl  # noqa: E402
from autopilot.models import Job  # noqa: E402


def job(**kw):
    base = dict(source="web:linkedin.com", title="DevOps Engineer", company="Acme", url="https://x.example/1",
                description="We need a DevOps engineer for AWS and Terraform work.")
    base.update(kw)
    return Job(**base)


def test_contract_type():
    assert fl.contract_type(job(title="DevOps Engineer (Contract, 6 months)")) == "Contract"
    assert fl.contract_type(job(description="Freelance DevOps help needed for a migration.")) == "Freelance"
    assert fl.contract_type(job(description="Part-time, 20 hours/week, remote.")) == "Part-Time"
    assert fl.contract_type(job(description="C2H role with a product company")) == "Contract"
    assert fl.contract_type(job(description="Full-time role. Your employment contract will be shared.")) == ""
    assert fl.contract_type(job(description="Write smart contract tooling")) == ""
    assert fl.contract_type(job(), declared="part_time") == "Part-Time"


def test_rate():
    assert fl.rate_of(job(description="Pay: ₹1,500 - 2,000 per hour, remote")) == "₹1,500 - 2,000 per hour"
    assert fl.rate_of(job(description="Budget 12-18 LPA, contract")) == "12-18 LPA"
    assert fl.rate_of(job(description="Rate $40/hr")) == "$40/hr"
    assert fl.rate_of(job()) == "Not stated"


def test_excel_columns(tmp_path):
    import openpyxl

    lead = fl.Lead(job(hr_email="hr@acme.com"), "Contract", summary="AWS migration", status=fl.SENT)
    path = fl.write_excel([lead], tmp_path / "devops_leads_x.xlsx")
    ws = openpyxl.load_workbook(path)["Leads"]
    assert [c.value for c in ws[1]] == fl.COLUMNS
    assert [c.value for c in ws[2]][:3] == [1, "Acme", "DevOps Engineer"] and ws.cell(2, 10).value == "Sent"
