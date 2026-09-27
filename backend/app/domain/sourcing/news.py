"""What a news article is, and when it could have been read (Phase 15 Part 2A).

Master spec section 71 wants a ``NewsProvider`` and says news sentiment must
never create a trade by itself. Before any interpretation can be honest, the
article has to be a real, attributable, licensed publication, and a decision
must see only what had been published by then. This module is that contract.
It interprets nothing.

## What an article carries

Its identity at the publisher, the publisher, the headline and URL as
published, the publication time, the moment this system could first read it
(``available_at``), what it is associated with and on whose word, its status
(published, corrected, retracted) and revision, the usage rights the licence
grants, and its source as a verified fact.

## What an article never carries

An impact, a direction, a sentiment or a relevance score. Those are
interpretations (section 71's classification belongs to a later, explicitly
labelled analysis layer), and a field for them here would invite a provider's
or a model's guess to be stored as if it were part of the article. An
association says only that the *provider or an operator* tagged the article
with an instrument; nothing infers one from a headline.

## Time and revisions

A decision at time T sees each article in the latest revision whose
``available_at <= T``. An article published after T does not exist for it. A
retraction is a revision like any other: it is shown as ``RETRACTED``,
explicitly - never silently dropped, never left looking live - and a
retraction published after T does not reach back into T.

## Real or not

``source.status`` says whether an article came from a verified, licensed
source. A ``TEST_FIXTURE`` or ``MOCK_DATA`` article is never real news and is
refused by :func:`news_visible_at` unless a caller explicitly asks to include
non-real articles - which only a test does.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique

from app.domain.common.verification import VerifiedValue

__all__ = [
    "ArticleStatus",
    "AssociationBasis",
    "NewsArticle",
    "NewsAssociation",
    "UsageRights",
    "news_visible_at",
]


@unique
class ArticleStatus(StrEnum):
    PUBLISHED = "PUBLISHED"
    CORRECTED = "CORRECTED"
    RETRACTED = "RETRACTED"


@unique
class UsageRights(StrEnum):
    """What the licence allows this system to do with the article."""

    DISPLAY_AND_ANALYSIS = "DISPLAY_AND_ANALYSIS"
    HEADLINE_AND_LINK_ONLY = "HEADLINE_AND_LINK_ONLY"
    ANALYSIS_ONLY = "ANALYSIS_ONLY"
    """May be read by analysis, not shown."""
    NONE = "NONE"
    """Not licensed for use here. The article is refused."""


@unique
class AssociationBasis(StrEnum):
    PROVIDER_TAGGED = "PROVIDER_TAGGED"
    OPERATOR_TAGGED = "OPERATOR_TAGGED"


@dataclass(frozen=True, slots=True)
class NewsAssociation:
    target: str
    """An instrument, underlying, sector or market identifier."""
    basis: AssociationBasis


@dataclass(frozen=True, slots=True)
class NewsArticle:
    article_id: str
    publisher: str
    headline: str
    url: str
    published_at: datetime
    available_at: datetime
    """When this system could first read this revision. Never earlier than
    publication: nobody reads an article before it exists."""

    source: VerifiedValue[str]
    usage: UsageRights
    associations: tuple[NewsAssociation, ...] = ()
    status: ArticleStatus = ArticleStatus.PUBLISHED
    revision: int = 0

    def __post_init__(self) -> None:
        for name in ("article_id", "publisher", "headline", "url"):
            if not getattr(self, name).strip():
                raise ValueError(f"an article has a {name}")
        if not self.url.startswith("https://"):
            raise ValueError("an article's URL is an https link to the publication")
        for name in ("published_at", "available_at"):
            if getattr(self, name).utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.available_at < self.published_at:
            raise ValueError("an article is not available before it is published")
        if self.revision < 0:
            raise ValueError("revision is non-negative")
        if self.revision == 0 and self.status is not ArticleStatus.PUBLISHED:
            raise ValueError("a correction or retraction is a later revision")

    @property
    def is_real(self) -> bool:
        s = self.source
        return s.is_authoritative and bool(s.source.strip()) and s.as_of is not None

    @property
    def usable_for_analysis(self) -> bool:
        return self.status is not ArticleStatus.RETRACTED and self.usage in (
            UsageRights.DISPLAY_AND_ANALYSIS,
            UsageRights.ANALYSIS_ONLY,
        )


def news_visible_at(
    articles: Iterable[NewsArticle],
    *,
    decision_time: datetime,
    include_non_real: bool = False,
) -> tuple[NewsArticle, ...]:
    """Each article as a decision at ``decision_time`` could have seen it.

    One entry per article - its latest revision available by then - ordered
    by publication time. Retracted articles stay in the result, marked. Not
    licensed for use (``UsageRights.NONE``) and, unless asked, non-real
    articles are excluded entirely.
    """
    if decision_time.utcoffset() is None:
        raise ValueError("decision_time must be timezone-aware")
    latest: dict[tuple[str, str], NewsArticle] = {}
    for article in articles:
        if article.available_at > decision_time or article.usage is UsageRights.NONE:
            continue
        if not article.is_real and not include_non_real:
            continue
        key = (article.publisher, article.article_id)
        held = latest.get(key)
        if held is None or article.revision > held.revision:
            latest[key] = article
        elif article.revision == held.revision and article != held:
            raise ValueError("two different articles share one identity and revision")
    return tuple(sorted(latest.values(), key=lambda a: (a.published_at, a.article_id)))
