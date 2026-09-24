from __future__ import annotations

import argparse
import json
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _cff_scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip("\"'")


def _contains_identifier(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return any(_contains_identifier(item, expected) for item in value)
    if isinstance(value, dict):
        return any(_contains_identifier(item, expected) for item in value.values())
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate AgentWeave release metadata.")
    parser.add_argument("--tag", help="Git tag, for example v0.7.0")
    args = parser.parse_args()

    pyproject = tomllib.loads(_read("pyproject.toml"))
    package_version = pyproject["project"]["version"]

    init_text = _read("agentweave/__init__.py")
    init_match = re.search(
        r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", init_text, re.MULTILINE
    )
    assert init_match, "agentweave.__version__ not found"
    init_version = init_match.group(1)

    cff_text = _read("CITATION.cff")
    cff_version = _cff_scalar(cff_text, "version")
    assert cff_version, "CITATION.cff version not found"

    codemeta = json.loads(_read("codemeta.json"))
    codemeta_version = str(codemeta.get("version", ""))
    assert codemeta.get("@context") == "https://w3id.org/codemeta/3.1", (
        "codemeta.json must use the CodeMeta 3.1 context"
    )

    versions = {
        "pyproject.toml": package_version,
        "agentweave.__version__": init_version,
        "CITATION.cff": cff_version,
        "codemeta.json": codemeta_version,
    }
    assert len(set(versions.values())) == 1, f"version mismatch: {versions}"

    if args.tag:
        expected_tag = f"v{package_version}"
        assert args.tag == expected_tag, (
            f"tag {args.tag} does not match package version {expected_tag}"
        )

    archive_registry = json.loads(_read("docs/zenodo_releases.json"))
    releases = archive_registry.get("releases", {})
    current_archive = releases.get(package_version)

    cff_doi = _cff_scalar(cff_text, "doi")
    cff_artifact = _cff_scalar(cff_text, "repository-artifact")
    codemeta_text = json.dumps(codemeta, sort_keys=True)

    if current_archive:
        expected_doi = current_archive["version_doi"]
        expected_record_url = current_archive["record_url"]
        expected_doi_url = f"https://doi.org/{expected_doi}"

        assert cff_doi == expected_doi, (
            f"CITATION.cff DOI {cff_doi!r} does not match archived release {expected_doi!r}"
        )
        assert cff_artifact == expected_record_url, (
            "CITATION.cff repository-artifact does not match the archived Zenodo record"
        )
        assert expected_doi in cff_text, "CITATION.cff is missing the version DOI identifier"
        assert _contains_identifier(codemeta.get("identifier"), expected_doi_url), (
            "codemeta.json identifier does not match the archived version DOI"
        )
    else:
        stale_values: list[str] = []
        for metadata in releases.values():
            stale_values.extend(
                [
                    metadata["version_doi"],
                    f"https://doi.org/{metadata['version_doi']}",
                    metadata["record_url"],
                ]
            )

        leaked = [
            value
            for value in stale_values
            if value and (value in cff_text or value in codemeta_text)
        ]
        assert not leaked, (
            "stale Zenodo metadata from an older release is present in release metadata: "
            + ", ".join(sorted(set(leaked)))
            + ". Remove old version-specific DOI/archive fields before tagging this version; "
            "after Zenodo archives the release, add its DOI to docs/zenodo_releases.json, "
            "CITATION.cff, and codemeta.json."
        )

    concept_doi = archive_registry.get("concept_doi")
    if concept_doi:
        concept_doi_url = f"https://doi.org/{concept_doi}"
        assert _contains_identifier(codemeta.get("sameAs"), concept_doi_url) or _contains_identifier(
            codemeta.get("identifier"), concept_doi_url
        ), "codemeta.json must expose the Concept DOI for the evolving project"

    print("Release metadata consistent:", json.dumps(versions, sort_keys=True))


if __name__ == "__main__":
    main()
