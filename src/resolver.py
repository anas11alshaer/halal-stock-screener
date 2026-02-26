"""Conflict resolution logic for multiple compliance sources."""

import logging
from typing import Optional

from scrapers import ScreeningResult, ComplianceStatus

logger = logging.getLogger(__name__)

# Priority order (most restrictive wins): NOT_HALAL > DOUBTFUL > HALAL > NOT_COVERED > ERROR
STATUS_PRIORITY = {
    ComplianceStatus.NOT_HALAL: 1,
    ComplianceStatus.DOUBTFUL: 2,
    ComplianceStatus.HALAL: 3,
    ComplianceStatus.NOT_COVERED: 4,
    ComplianceStatus.ERROR: 5,
}

STATUS_DISPLAY = {
    ComplianceStatus.NOT_HALAL: "Not Halal",
    ComplianceStatus.DOUBTFUL: "Doubtful",
    ComplianceStatus.HALAL: "Halal",
    ComplianceStatus.NOT_COVERED: "Not Covered",
    ComplianceStatus.ERROR: "Error",
}


def resolve_compliance(
    musaffa: Optional[ScreeningResult],
    zoya: Optional[ScreeningResult]
) -> tuple[ScreeningResult, bool]:
    """
    Resolve compliance status from multiple sources.

    Priority (most restrictive wins): NOT_HALAL > DOUBTFUL > HALAL > NOT_COVERED > ERROR

    Rules:
    - Both agree -> return agreed status
    - Conflict -> return more restrictive status
    - One NOT_COVERED/ERROR -> use the other source's result
    - Both NOT_COVERED -> NOT_COVERED
    - Both ERROR -> ERROR

    Args:
        musaffa: Result from Musaffa scraper (or None)
        zoya: Result from Zoya scraper (or None)

    Returns:
        Tuple of (final_result, is_conflict)
    """
    # Handle cases where one or both sources are missing
    if musaffa is None and zoya is None:
        raise ValueError("At least one source result must be provided")

    if musaffa is None:
        return (zoya, False)

    if zoya is None:
        return (musaffa, False)

    ticker = musaffa.ticker  # Both should have the same ticker

    musaffa_status = musaffa.status
    zoya_status = zoya.status

    # Both agree
    if musaffa_status == zoya_status:
        # Prefer Musaffa's result as it may have more details
        final_result = ScreeningResult(
            ticker=ticker,
            status=musaffa_status,
            source="combined",
            compliance_ranking=musaffa.compliance_ranking,
            company_name=musaffa.company_name,
            details=_combine_details(musaffa.details, zoya.details)
        )
        logger.info(f"{ticker}: Both sources agree - {musaffa_status.value}")
        return (final_result, False)

    # One source has NOT_COVERED or ERROR - use the other
    # Always preserve company_name from whichever source has it
    if musaffa_status in (ComplianceStatus.NOT_COVERED, ComplianceStatus.ERROR):
        if zoya_status not in (ComplianceStatus.NOT_COVERED, ComplianceStatus.ERROR):
            logger.info(f"{ticker}: Using Zoya result ({zoya_status.value}) since Musaffa is {musaffa_status.value}")
            result = ScreeningResult(
                ticker=ticker,
                status=zoya_status,
                source="zoya",
                company_name=musaffa.company_name or zoya.company_name,
                details=zoya.details,
            )
            return (result, False)

    if zoya_status in (ComplianceStatus.NOT_COVERED, ComplianceStatus.ERROR):
        if musaffa_status not in (ComplianceStatus.NOT_COVERED, ComplianceStatus.ERROR):
            logger.info(f"{ticker}: Using Musaffa result ({musaffa_status.value}) since Zoya is {zoya_status.value}")
            return (musaffa, False)

    # Both NOT_COVERED
    if musaffa_status == ComplianceStatus.NOT_COVERED and zoya_status == ComplianceStatus.NOT_COVERED:
        final_result = ScreeningResult(
            ticker=ticker,
            status=ComplianceStatus.NOT_COVERED,
            source="combined",
            error_message="Stock not covered by either source"
        )
        return (final_result, False)

    # Both ERROR
    if musaffa_status == ComplianceStatus.ERROR and zoya_status == ComplianceStatus.ERROR:
        final_result = ScreeningResult(
            ticker=ticker,
            status=ComplianceStatus.ERROR,
            source="combined",
            error_message="Both sources returned errors"
        )
        return (final_result, False)

    # Conflict - use most restrictive (lower priority number wins)
    musaffa_priority = STATUS_PRIORITY.get(musaffa_status, 99)
    zoya_priority = STATUS_PRIORITY.get(zoya_status, 99)

    if musaffa_priority <= zoya_priority:
        more_restrictive = musaffa_status
        winning_source = "musaffa"
    else:
        more_restrictive = zoya_status
        winning_source = "zoya"

    logger.warning(
        f"{ticker}: CONFLICT - Musaffa={musaffa_status.value}, Zoya={zoya_status.value}. "
        f"Using more restrictive: {more_restrictive.value}"
    )

    final_result = ScreeningResult(
        ticker=ticker,
        status=more_restrictive,
        source="combined",
        compliance_ranking=musaffa.compliance_ranking if winning_source == "musaffa" else None,
        company_name=musaffa.company_name if winning_source == "musaffa" else None,
        details=f"Conflict: Musaffa={STATUS_DISPLAY[musaffa_status]}, Zoya={STATUS_DISPLAY[zoya_status]}"
    )

    return (final_result, True)


def _combine_details(musaffa_details: Optional[str], zoya_details: Optional[str]) -> Optional[str]:
    """Combine details from both sources."""
    parts = []
    if musaffa_details:
        parts.append(f"Musaffa: {musaffa_details}")
    if zoya_details:
        parts.append(f"Zoya: {zoya_details}")
    return "; ".join(parts) if parts else None
