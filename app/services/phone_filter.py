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

    Parsing strategy:
    - Numbers starting with '+' or '0' are parsed as-is with EG default
      (covers Egyptian local format 01X... and proper international format).
    - Numbers starting with any other digit are tried first with a '+' prefix
      so that bare country codes (e.g. "962...", "91...") are identified
      correctly instead of being mis-tagged as Egyptian.
    """
    try:
        if not phone_str.startswith(("+", "0")):
            try:
                parsed = phonenumbers.parse("+" + phone_str, None)
                region = phonenumbers.region_code_for_number(parsed)
                if region:
                    return region in _ALLOWED_REGIONS, region
            except Exception:
                pass
        parsed = phonenumbers.parse(phone_str, "EG")
        region = phonenumbers.region_code_for_number(parsed)
        if not region:
            return True, ""
        return region in _ALLOWED_REGIONS, region
    except Exception:
        return True, ""
