"""Quality filters and traffic heuristics."""
from urllib.parse import urlparse

BLOCKLIST = {
    "bbc.com", "cnn.com", "aljazeera.com", "foxnews.com", "nytimes.com",
    "theguardian.com", "reuters.com", "apnews.com", "bloomberg.com",
    "forbes.com", "techcrunch.com", "mashable.com", "buzzfeed.com",
    "huffpost.com", "dailymail.co.uk", "mirror.co.uk", "sky.com",
    "mbc.net", "rotana.net", "osn.com", "shahid.net", "anghami.com",
    "wikipedia.org", "youtube.com", "facebook.com", "twitter.com",
    "instagram.com", "linkedin.com", "reddit.com", "pinterest.com",
    "amazon.com", "google.com", "microsoft.com", "apple.com",
    "medium.com", "wordpress.com", "blogspot.com", "tumblr.com",
    "wix.com", "squarespace.com", "weebly.com", "shopify.com",
    "substack.com", "ghost.io", "beehiiv.com",
}

# Free hosting / platform subdomains — skip if the domain matches these
PLATFORM_DOMAINS = {
    "medium.com", "wordpress.com", "blogspot.com", "tumblr.com",
    "wix.com", "weebly.com", "squarespace.com", "ghost.io",
    "beehiiv.com", "substack.com",
}


def normalise_domain(url: str) -> str:
    """Return scheme + netloc stripped of www., lowercased, no trailing slash."""
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
        host = parsed.netloc.lower().lstrip("www.")
        return host
    except Exception:
        return url.lower().strip("/")


def is_blocked(domain: str) -> bool:
    """Return True if domain is in blocklist or is a subdomain of a platform."""
    domain = domain.lower()
    if domain in BLOCKLIST:
        return True
    for platform in PLATFORM_DOMAINS:
        if domain == platform or domain.endswith("." + platform):
            return True
    return False


def guess_traffic(html: str, soup) -> str:
    """
    Heuristic traffic estimate based on page signals.
    High:   sitemap + 5+ social links + comments + multiple authors
    Medium: sitemap OR social links present
    Low:    bare site
    """
    text = html.lower()

    has_sitemap = "sitemap" in text or "sitemap.xml" in text
    social_keywords = ["twitter.com", "facebook.com", "instagram.com",
                       "linkedin.com", "youtube.com", "tiktok.com"]
    social_count = sum(1 for s in social_keywords if s in text)
    has_comments = any(k in text for k in ["disqus", "comments", "reply", "leave a comment"])
    has_multiple_authors = text.count("author") >= 3 or text.count("by ") >= 4

    score = 0
    if has_sitemap:
        score += 2
    if social_count >= 5:
        score += 2
    elif social_count >= 2:
        score += 1
    if has_comments:
        score += 1
    if has_multiple_authors:
        score += 1

    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def detect_ads(html: str) -> bool:
    """Return True if page contains common ad network signals."""
    signals = [
        "adsbygoogle", "googletag", "doubleclick.net",
        "admanager", "prebid", "amazon-adsystem",
        "media.net", "taboola", "outbrain",
        'class="adsbygoogle"', "adsense",
    ]
    html_lower = html.lower()
    return any(s in html_lower for s in signals)
