#!/usr/bin/env python3
"""Title-level noise filter for the apply farm.

Purges non-tech roles (fundraising, marketing, HR, sales, etc.) at injection
time so workers never see them. Also catches Wellfound category-listing titles
("Remote X Jobs" = a /role/r/ search page, not a real job).

Usage:
    from title_filter import is_tech_title
    if not is_tech_title(title): skip
"""
import re

# Case-insensitive blacklist. Careful with word boundaries so "Marketplace"
# or "Sales Engineer" don't get caught.
BLACKLIST = re.compile(
    r"(?:"
    r"fundraising|fund.?raise|"
    r"marketing|marketer|"
    r"social\s*media|"
    r"content\s*(?:writer|writing|creator|manager)|copywriter|"
    r"seo\s*(?:specialist|executive|analyst|manager)?|"
    r"\bhr\b|human\s*resource|"
    r"recruit\w*|talent\s*acquisition|people\s*(?:ops|operations)|"
    r"sales\b(?!\s*(?:engineer|developer|dev|architect|ops))|"
    r"business\s*development|account\s*manager|"
    r"customer\s*(?:support|success|service)|"
    r"data\s*entry|telecall\w*|telesales|"
    r"operations\s*(?:executive|manager|intern|associate)|"
    r"^operations$|"
    r"admin\s*(?:assistant|support)?|receptionist|back\s*office|"
    r"business\s*analyst|"
    r"fashion|merchandis|"
    r"\blaw\b|legal|articleship|"
    r"accounting|taxation|\bcma\b|"
    r"video\s*edit|"
    r"\bdriver\b|"
    r"financial\s*(?:solutions|advisor)|"
    r"skillbridge|geotechnical|"
    r"secret\s*clearance"
    r")",
    re.IGNORECASE,
)

# Wellfound category-listing titles: "Remote Sales Manager Jobs" / "HR Jobs"
CATEGORY_PAGE = re.compile(r"^\s*(?:remote\s+)?[\w\s&/-]{1,40}\s+jobs?\s*$", re.IGNORECASE)

# Job feeds contain every profession. A blacklist cannot enumerate that universe
# safely, so target portals also require an explicit software/data/product-design
# signal. YC company pages are intentionally exempt; individual YC job H1s use
# source="yc-job" in the worker.
TECH_SIGNAL = re.compile(
    r"(?:software|web\s*(?:development|developer|engineer)|full[ -]?stack|"
    r"front[ -]?end|back[ -]?end|developer|programming|react|angular|vue|"
    r"node(?:\.js)?|javascript|typescript|python|django|flask|fastapi|java\b|"
    r"\.net\b|php\b|ruby(?: on rails)?|golang|\bgo developer|rust\b|"
    r"mobile\s*(?:app|development|engineer)|android|ios\b|devops|sre\b|"
    r"site reliability|cloud\s*(?:engineer|developer)|platform\s*engineer|"
    r"database\s*(?:engineer|developer)|sql\b|data\s*(?:science|scientist|"
    r"analytics|analyst|analysis|engineering|engineer)|machine\s*learning|"
    r"artificial\s*intelligence|\bai(?:/ml)?\b|\bml engineer|cyber\s*security|"
    r"security\s*engineer|quality\s*assurance|\bqa(?:/qc)?\b|test\s*(?:engineer|"
    r"automation)|ui[/ -]?ux|product\s*(?:design|engineer)|design\s*systems?\s*engineer|"
    r"blockchain|web3|smart\s*contract|threat\s*research|"
    r"solana|sales\s*engineer|solutions?\s*engineer|application\s*engineer)",
    re.IGNORECASE,
)

SENIORITY = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|lead|manager|director|head|architect|"
    r"vp|vice president|chief|cto|co-founder|founder|founding)\b",
    re.IGNORECASE,
)


def title_rejection_reason(title: str, source: str = "") -> str | None:
    """Return a stable rejection code, or None for an in-scope title."""
    source = (source or "").lower()
    strict = (
        source in {"linkedin", "internshala", "himalayas", "weworkremotely", "yc-job"}
        or source.startswith("wellfound")
    )
    if not title:
        return "missing-title" if strict else None
    if BLACKLIST.search(title):
        return "non-tech-title"
    if SENIORITY.search(title):
        return "seniority-title"
    if strict and not TECH_SIGNAL.search(title):
        return "non-tech-title"
    return None


def is_tech_title(title: str, source: str = "") -> bool:
    """Return True if the job title passes the tech filter."""
    if title_rejection_reason(title, source):
        return False
    # Only category listings carry this shape (real job titles rarely end in
    # bare "Jobs" with no other punctuation)
    if source.startswith("wellfound") and CATEGORY_PAGE.match(title):
        return False
    return True


if __name__ == "__main__":
    # quick self-test
    bad = ["Fundraising Internship", "Marketing", "Sales", "HR Manager",
           "Remote Sales Manager Jobs", "Content Writer", "Social Media Intern",
           "Business Development Executive", "Growth Marketer Jobs"]
    good = ["Sales Engineer", "Backend Developer", "React Developer",
            "Software Engineer Intern", "DevOps Engineer", "Data Engineer",
            "QA Automation Engineer"]
    bad += ["Senior Full Stack Engineer", "Solutions Architect", "Civil Engineering"]
    for t in bad:
        assert not is_tech_title(t, "wellfound"), f"should reject: {t}"
    for t in good:
        assert is_tech_title(t, "wellfound"), f"should accept: {t}"
    print("title_filter self-test OK")
