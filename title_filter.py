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


def is_tech_title(title: str, source: str = "") -> bool:
    """Return True if the job title passes the tech filter."""
    if not title:
        return True  # unknown titles get a shot; URL-level checks handle the rest
    if BLACKLIST.search(title):
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
    good = ["Senior Full Stack Engineer II, Marketplace (Backend Leaning)",
            "Sales Engineer", "Backend Developer", "React Developer",
            "Software Engineer Intern", "DevOps Engineer", "Data Engineer",
            "Solutions Architect"]
    for t in bad:
        assert not is_tech_title(t, "wellfound"), f"should reject: {t}"
    for t in good:
        assert is_tech_title(t, "wellfound"), f"should accept: {t}"
    print("title_filter self-test OK")
