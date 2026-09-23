"""Deterministic relationship grouping without models or embeddings."""

import hashlib
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .candidate_finder import normalized_tokens
from .candidate_models import CandidateFile, CandidateGroup


_NAME_STOPWORDS = frozenset({
    "a", "al", "de", "del", "el", "en", "la", "las", "los", "the", "of", "and",
    "documento", "archivo", "file", "pdf", "docx", "txt",
})
_SEQUENCE = re.compile(r"^([a-z]+)[\s_-]*(\d{1,6})(?:\D|$)", re.IGNORECASE)


def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in folded if not unicodedata.combining(character))


def _name_tokens(file: CandidateFile) -> frozenset[str]:
    tokens = normalized_tokens(Path(file.name).stem)
    return frozenset(token for token in tokens if token not in _NAME_STOPWORDS and not token.isdigit() and len(token) > 1)


def _sequence(file: CandidateFile) -> tuple[str, int] | None:
    match = _SEQUENCE.match(_normalized(Path(file.name).stem))
    return (match.group(1), int(match.group(2))) if match else None


def _timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, OverflowError):
        return 0.0


def _normalized_set(values: tuple[str, ...]) -> frozenset[str]:
    return frozenset(_normalized(value).strip() for value in values if value.strip())


def _shared_display(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    right_values = _normalized_set(right)
    return tuple(value for value in left if _normalized(value).strip() in right_values)


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


class RelationshipGrouper:
    """Build related sets from explainable local signals in near-linear time."""

    THRESHOLD = 0.55
    NEIGHBORS_PER_DIRECTORY = 8

    @staticmethod
    def _pair_score(left: CandidateFile, right: CandidateFile) -> tuple[float, tuple[str, ...]]:
        score = 0.0
        evidence: list[str] = []
        left_sequence, right_sequence = _sequence(left), _sequence(right)
        if left_sequence and right_sequence and left_sequence[0] == right_sequence[0] and abs(left_sequence[1] - right_sequence[1]) <= 20:
            score += 0.50
            evidence.append(f"Forman la secuencia {left_sequence[0].upper()}{left_sequence[1]:02d}–{right_sequence[0].upper()}{right_sequence[1]:02d}.")
        if left.topic and right.topic and _normalized(left.topic) == _normalized(right.topic):
            score += 0.55
            evidence.append(f"Comparten el tema «{left.topic}».")
        shared_tags = _normalized_set(left.tags) & _normalized_set(right.tags)
        if shared_tags:
            score += min(0.25, 0.12 * len(shared_tags))
            evidence.append("Comparten etiquetas: " + ", ".join(_shared_display(left.tags, right.tags)) + ".")
        shared_keywords = _normalized_set(left.keywords) & _normalized_set(right.keywords)
        if shared_keywords:
            score += min(0.30, 0.10 * len(shared_keywords))
            evidence.append("Comparten palabras clave: " + ", ".join(_shared_display(left.keywords, right.keywords)[:4]) + ".")
        common_name = _name_tokens(left) & _name_tokens(right)
        if common_name:
            score += min(0.20, 0.10 * len(common_name))
            evidence.append("Sus nombres comparten: " + ", ".join(sorted(common_name)[:3]) + ".")
        if left.source_directory == right.source_directory:
            score += 0.12
            evidence.append("Proceden de la misma carpeta.")
        delta = abs(_timestamp(left.detected_at) - _timestamp(right.detected_at))
        if delta <= 48 * 3600:
            score += 0.12
            evidence.append("Fueron detectados en un periodo cercano.")
        elif delta <= 7 * 24 * 3600:
            score += 0.06
        if left.category == right.category or left.extension == right.extension:
            score += 0.08
            evidence.append("Tienen un tipo de archivo compatible.")
        if "content" in left.matched_by and "content" in right.matched_by:
            score += 0.08
            evidence.append("Ambos coinciden mediante contenido indexado.")
        return round(score, 4), tuple(evidence)

    @staticmethod
    def _neighbor_pairs(files: tuple[CandidateFile, ...]) -> set[tuple[int, int]]:
        pairs: set[tuple[int, int]] = set()
        buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
        for index, file in enumerate(files):
            sequence = _sequence(file)
            if sequence:
                buckets[("sequence", f"{file.source_directory}|{sequence[0]}|{file.category}")].append(index)
            if file.topic:
                buckets[("topic", _normalized(file.topic))].append(index)
            for tag in _normalized_set(file.tags):
                buckets[("tag", f"{file.source_directory}|{tag}")].append(index)
            for keyword in _normalized_set(file.keywords):
                buckets[("keyword", f"{file.source_directory}|{keyword}")].append(index)
            buckets[("directory", str(file.source_directory))].append(index)

        for (kind, _key), indices in buckets.items():
            if kind == "directory":
                indices.sort(key=lambda item: (_timestamp(files[item].detected_at), files[item].name.casefold(), files[item].file_id))
                for position, left in enumerate(indices):
                    for right in indices[position + 1:position + 1 + RelationshipGrouper.NEIGHBORS_PER_DIRECTORY]:
                        pairs.add((min(left, right), max(left, right)))
            else:
                indices.sort(key=lambda item: (files[item].name.casefold(), files[item].file_id))
                for left, right in zip(indices, indices[1:]):
                    pairs.add((min(left, right), max(left, right)))
        return pairs

    @staticmethod
    def _intersection(files: tuple[CandidateFile, ...], attribute: str) -> tuple[str, ...]:
        value_sets = [_normalized_set(getattr(file, attribute)) for file in files]
        if not value_sets or any(not values for values in value_sets):
            return ()
        common = set.intersection(*(set(values) for values in value_sets))
        first_values = getattr(files[0], attribute)
        return tuple(value for value in first_values if _normalized(value).strip() in common)

    def group(self, files: tuple[CandidateFile, ...] | list[CandidateFile]) -> tuple[CandidateGroup, ...]:
        candidates = tuple(files)
        if len(candidates) < 2:
            return ()
        sets = _DisjointSet(len(candidates))
        edge_scores: dict[tuple[int, int], tuple[float, tuple[str, ...]]] = {}
        for pair in sorted(self._neighbor_pairs(candidates)):
            score, evidence = self._pair_score(candidates[pair[0]], candidates[pair[1]])
            if score >= self.THRESHOLD:
                sets.union(*pair)
                edge_scores[pair] = (score, evidence)

        components: dict[int, list[int]] = defaultdict(list)
        for index in range(len(candidates)):
            components[sets.find(index)].append(index)

        groups: list[CandidateGroup] = []
        for indices in components.values():
            if len(indices) < 2:
                continue
            members = tuple(sorted((candidates[index] for index in indices), key=lambda item: (item.name.casefold(), item.file_id)))
            member_ids = {id(candidates[index]) for index in indices}
            component_edges = [value for (left, right), value in edge_scores.items()
                               if id(candidates[left]) in member_ids and id(candidates[right]) in member_ids]
            score = round(sum(item[0] for item in component_edges) / len(component_edges), 4) if component_edges else 0.0
            normalized_topics = tuple(_normalized(file.topic) for file in members if file.topic)
            topics = (members[0].topic,) if len(normalized_topics) == len(members) and len(set(normalized_topics)) == 1 else ()
            tags = self._intersection(members, "tags")
            keywords = self._intersection(members, "keywords")
            sequences = [_sequence(file) for file in members]
            detected = sorted(file.detected_at for file in members)
            evidence_items: list[str] = []
            if all(sequences) and len({item[0] for item in sequences if item}) == 1:
                numbers = sorted(item[1] for item in sequences if item)
                prefix = sequences[0][0].upper()
                evidence_items.append(f"Forman la secuencia {prefix}{numbers[0]:02d}–{prefix}{numbers[-1]:02d}.")
            if topics:
                evidence_items.append(f"Comparten el tema «{topics[0]}».")
            if tags:
                evidence_items.append("Comparten etiquetas: " + ", ".join(tags[:5]) + ".")
            if keywords:
                evidence_items.append("Comparten palabras clave: " + ", ".join(keywords[:5]) + ".")
            if len({file.source_directory for file in members}) == 1:
                evidence_items.append("Proceden de la misma carpeta.")
            if _timestamp(detected[-1]) - _timestamp(detected[0]) <= 48 * 3600:
                evidence_items.append("Fueron detectados en el mismo periodo de 48 horas.")
            if len({file.category for file in members}) == 1 or len({file.extension for file in members}) == 1:
                evidence_items.append("Tienen tipos de archivo compatibles.")
            if all("content" in file.matched_by for file in members):
                evidence_items.append("Todos coinciden mediante contenido indexado.")
            evidence = tuple(evidence_items)
            if topics:
                label = topics[0]
            elif keywords:
                label = " · ".join(keywords[:3])
            elif tags:
                label = " · ".join(tags[:3])
            elif all(sequences) and len({item[0] for item in sequences if item}) == 1:
                label = f"Secuencia {sequences[0][0].upper()}"
            else:
                label = f"{members[0].category} relacionados"
            digest = hashlib.sha256(",".join(str(file.file_id) for file in members).encode()).hexdigest()[:12]
            groups.append(CandidateGroup(
                f"group-{digest}", members, label, evidence, score, topics, tags,
                (detected[0], detected[-1]),
            ))
        groups.sort(key=lambda group: (-len(group.files), -group.score, group.id))
        return tuple(groups)
