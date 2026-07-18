import phonenumbers

_ALLOWED_REGIONS = {
    # Arab League (core)
    "EG", "SA", "AE", "JO", "LB", "KW", "QA", "BH", "OM", "IQ",
    "LY", "MA", "TN", "DZ", "SD", "SY", "YE", "PS",
    # Europe / West
    "GB", "IE", "PL", "RU", "FR", "DE", "IT", "ES", "NL",
    "BE", "CZ", "HU", "RO", "UA", "GR", "PT",
    # Americas / Oceania
    "US", "CA", "AU",
}


def check_phone_region(phone_str: str) -> tuple[bool, str]:
    """
    Returns (allowed, region_code).
    Fails open — unparseable or unknown region is treated as allowed
    to avoid false positives on local-format numbers.
    Parses with EG as default region so Egyptian local numbers (010...)
    resolve correctly without a +20 prefix.
    """
    try:
        parsed = phonenumbers.parse(phone_str, "EG")
        region = phonenumbers.region_code_for_number(parsed)
        if not region:
            return True, ""
        return region in _ALLOWED_REGIONS, region
    except Exception:
        return True, ""
