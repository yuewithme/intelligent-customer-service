from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings


_LEGACY_TABLES = (
    "talk_script_match_logs",
    "template_library",
    "question_cluster",
    "scene_index",
)


def purge_legacy_talk_script_data() -> None:
    """Keep the removed sales-script library empty on existing deployments."""
    engine = create_engine(get_settings().database_url)
    existing_tables = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        for table_name in _LEGACY_TABLES:
            if table_name in existing_tables:
                connection.execute(text(f'DELETE FROM "{table_name}"'))
    engine.dispose()
