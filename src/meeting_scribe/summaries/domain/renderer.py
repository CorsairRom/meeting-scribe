"""Parse the model's body and render ``summary.md`` with a locally computed header (spec 11)."""

from pydantic import BaseModel, ConfigDict
from returns.result import Failure, Result, Success

from meeting_scribe.summaries.domain.errors import SummaryOutputInvalidError
from meeting_scribe.summaries.domain.headings import Headings, headings_for
from meeting_scribe.summaries.domain.request import SummaryRequest

SECTION_COUNT = 4


class SummaryBody(BaseModel):
    """What the model wrote: a one-line title and the four sections' content."""

    model_config = ConfigDict(frozen=True)

    title: str
    sections: tuple[str, str, str, str]


class SummaryText(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    markdown: str


def parse_body(output: str, headings: Headings) -> Result[SummaryBody, SummaryOutputInvalidError]:
    """Read the title and the four sections in order; anything else is output-invalid."""
    title = ""
    expected = [heading.casefold() for heading in headings.sections]
    contents: list[list[str]] = []
    for raw in output.splitlines():
        text = raw.strip()
        if (
            text.startswith("## ")
            and len(contents) < SECTION_COUNT
            and text[3:].strip().casefold() == expected[len(contents)]
        ):
            contents.append([])
        elif contents:
            contents[-1].append(raw)
        elif text.startswith("# ") and not title:
            title = text[2:].strip()
    if not title or len(contents) < SECTION_COUNT:
        return Failure(
            SummaryOutputInvalidError("The model output did not follow the summary format.")
        )
    sections = tuple("\n".join(part).strip() for part in contents)
    return Success(
        SummaryBody(title=title, sections=(sections[0], sections[1], sections[2], sections[3]))
    )


def render_summary(request: SummaryRequest, body: SummaryBody) -> SummaryText:
    """Compose the file: the facts come from the request, never from the model."""
    h = headings_for(request.language)
    minutes = max(1, round(request.duration_seconds / 60))
    started = request.started_at
    header = [
        f"# {body.title} - {started:%Y-%m-%d} ({started:%H:%M}, ~{minutes} min)",
        f"**{h.participants}:** {', '.join(request.participants)}",
        (
            f"**{h.generated_by}:** {request.provider_label} / {request.limits.model}"
            f" on {request.generated_at:%Y-%m-%d}"
        ),
        f"> {h.note}",
    ]
    parts = ["\n".join(header)]
    for heading, content in zip(h.sections, body.sections, strict=True):
        parts.append(f"## {heading}\n{content or h.none}")
    return SummaryText(title=body.title, markdown="\n\n".join(parts) + "\n")
