"""Provider-neutral voting policy for compliance results."""

import logging
from collections import Counter

from scrapers import ComplianceStatus, ScreeningResult

logger = logging.getLogger(__name__)


def resolve_compliance(
    *provider_results: ScreeningResult | None,
) -> tuple[ScreeningResult, bool]:
    """Resolve confirmed provider verdicts by majority; every tie is not halal."""
    results = [result for result in provider_results if result is not None]
    if not results:
        raise ValueError("At least one source result must be provided")

    confirmed = [result for result in results if result.is_confirmed]
    ticker = results[0].ticker
    company_name = next((result.company_name for result in results if result.company_name), None)

    if not confirmed:
        not_covered = all(result.status == ComplianceStatus.NOT_COVERED for result in results)
        status = ComplianceStatus.NOT_COVERED if not_covered else ComplianceStatus.ERROR
        return (
            ScreeningResult(
                ticker=ticker,
                status=status,
                source="combined",
                company_name=company_name,
                error_message="No provider returned a confirmed verdict",
            ),
            False,
        )

    counts = Counter(result.status for result in confirmed)
    highest_count = max(counts.values())
    winners = [status for status, count in counts.items() if count == highest_count]
    is_conflict = len(counts) > 1
    tied = len(winners) > 1
    final_status = ComplianceStatus.NOT_HALAL if tied else winners[0]
    provisional = len(confirmed) == 1

    details = ", ".join(f"{result.source}={result.status.value}" for result in confirmed)
    if tied:
        details = f"Tie resolved as NOT_HALAL; {details}"
    elif is_conflict:
        details = f"Majority vote; {details}"

    logger.info(
        "%s: resolved %s from %d confirmed providers%s",
        ticker,
        final_status.value,
        len(confirmed),
        " (provisional)" if provisional else "",
    )
    return (
        ScreeningResult(
            ticker=ticker,
            status=final_status,
            source="combined",
            company_name=company_name,
            details=details,
            is_provisional=provisional,
            confirmation_count=len(confirmed),
            asset_type=confirmed[0].asset_type,
            quote_type=confirmed[0].quote_type,
        ),
        is_conflict,
    )
