"""Non-timing performance regression with ten thousand indexed rows."""

from pathlib import Path

from ordenia.automation.candidate_finder import CandidateFinder
from ordenia.automation.candidate_models import CandidateQuery
from ordenia.automation.grouping import RelationshipGrouper
from ordenia.automation.models import RiskLevel
from ordenia.automation.policy import PolicyDecision
from ordenia.database.connection import connect
from ordenia.database.repositories import Repository


class _IndexOnlyPolicy:
    def evaluate(self, _file: object, _folder: object) -> PolicyDecision:
        return PolicyDecision(RiskLevel.NORMAL, "eligible", "Synthetic indexed row")


def test_ten_thousand_rows_retrieve_rank_group_and_remain_idempotent(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "performance.sqlite3")
    root = tmp_path / "synthetic"
    with connect(repository.db_path) as db:
        db.execute("""INSERT INTO watched_folders(
            id,path,enabled,removed,include_subfolders,destination_strategy,custom_destination,created_at)
            VALUES (1,?,1,0,1,'inside',NULL,'2026-09-20')""", (str(root),))
        rows = []
        for index in range(1, 10_001):
            name = f"S{index:05d}_base_de_datos.pdf"
            path = root / name
            rows.append((index, 1, str(path), str(path).casefold(), str(root), name, ".pdf", 100,
                         "Documentos", "2026-09-20T10:00:00", "2026-09-20T10:00:00", index,
                         "Pendiente", "active"))
        db.executemany("""INSERT INTO files(
            id,watched_folder_id,path,path_key,source_directory,name,extension,size,category,
            detected_at,modified_at,mtime_ns,status,index_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    finder = CandidateFinder(repository, _IndexOnlyPolicy())  # type: ignore[arg-type]
    query = CandidateQuery(text="base de datos", include_content=False, limit=10_000)
    first = finder.find(query)
    second = finder.find(query)
    assert first == second
    assert len(first.eligible) == 10_000
    groups = RelationshipGrouper().group(first.eligible)
    assert sum(len(group.files) for group in groups) == 10_000
    assert groups == RelationshipGrouper().group(first.eligible)
