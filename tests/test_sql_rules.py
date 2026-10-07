from archery_mcp.sql_rules import (
    review_release_sql,
    split_statements,
    strip_comments,
)

LEVEL_ERROR = 2
LEVEL_WARNING = 1
LEVEL_PASS = 0


def rules(sql: str) -> list[str]:
    return [
        code
        for row in review_release_sql(sql)["rows"]
        for code in row["rule"]
    ]


def levels(sql: str) -> list[int]:
    return [row["errlevel"] for row in review_release_sql(sql)["rows"]]


def test_split_respects_strings_comments_and_dollar_quotes():
    sql = (
        "-- 说明; 文本\n"
        "UPDATE t SET name = 'a;b' WHERE id = 1;\n"
        "/* 块注释 ; */ INSERT INTO t (a) VALUES (1);\n"
        "DO $$ BEGIN RAISE NOTICE ';'; END $$;\n"
        "SELECT 1"
    )
    statements = split_statements(sql)
    assert len(statements) == 4
    assert "UPDATE" in statements[0]
    assert "'a;b'" in statements[0]
    assert "INSERT" in statements[1]
    assert "RAISE NOTICE ';'" in statements[2]
    assert statements[3] == "SELECT 1"


def test_strip_comments_keeps_string_content():
    assert strip_comments("-- 注释\nSELECT '1 -- 2'") == "SELECT '1 -- 2'"
    assert strip_comments("SELECT 1 /* ; */ + 2") == "SELECT 1 + 2"


def test_plain_insert_passes():
    sql = "INSERT INTO app_users (name) VALUES ('示例应用A');"
    result = review_release_sql(sql)
    assert result["error_count"] == 0
    assert result["warning_count"] == 0
    assert result["statement_count"] == 1
    assert result["syntax_type"] == 2
    assert levels(sql) == [LEVEL_PASS]


def test_alter_ddl_passes_and_reports_syntax_type():
    sql = "ALTER TABLE app_users ADD COLUMN remark text;"
    result = review_release_sql(sql)
    assert result["error_count"] == 0
    assert result["syntax_type"] == 1


def test_select_is_rejected():
    sql = "SELECT * FROM app_users;"
    assert "select_forbidden" in rules(sql)
    assert review_release_sql(sql)["error_count"] == 1


def test_cte_select_is_rejected_and_flagged_complex():
    sql = "WITH c AS (SELECT id FROM t) SELECT * FROM c;"
    assert "select_forbidden" in rules(sql)
    assert "complex_syntax" in rules(sql)


def test_update_delete_without_where_rejected():
    assert "missing_where" in rules("UPDATE t SET a = 1;")
    assert "missing_where" in rules("DELETE FROM t;")
    assert "missing_where" not in rules("UPDATE t SET a = 1 WHERE id = 2;")
    assert "missing_where" not in rules("DELETE FROM t WHERE a = 'where';")


def test_procedure_function_trigger_do_rejected():
    assert "complex_block" in rules("CREATE FUNCTION f() RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;")
    assert "complex_block" in rules("CREATE PROCEDURE p() LANGUAGE SQL BEGIN END;")
    assert "complex_block" in rules("DO $$ BEGIN RAISE NOTICE 'hi'; END $$;")
    assert "complex_block" in rules("CREATE TRIGGER trg AFTER INSERT ON t FOR EACH ROW EXECUTE FUNCTION f();")


def test_explicit_transaction_warns():
    for statement in ("BEGIN;", "START TRANSACTION;", "COMMIT;", "ROLLBACK;", "SAVEPOINT s1;", "END;"):
        assert "explicit_transaction" in rules(statement), statement
    assert "explicit_transaction" not in rules("INSERT INTO t (a) VALUES (1);")


def test_temporary_table_warns():
    assert "temp_table" in rules("CREATE TEMP TABLE tmp1 (id int);")
    assert "temp_table" in rules("CREATE TEMPORARY TABLE tmp1 (id int);")
    assert "temp_table" not in rules("CREATE TABLE tmp1 (id int);")


def test_complex_query_syntax_warns():
    assert "complex_syntax" in rules("INSERT INTO t SELECT row_number() OVER (ORDER BY id) FROM s;")
    assert "complex_syntax" in rules("CREATE TABLE t AS SELECT * FROM s;")
    assert "complex_syntax" in rules("LOCK TABLE t IN EXCLUSIVE MODE;")
    assert "complex_syntax" not in rules("INSERT INTO t (a) VALUES (1);")


def test_cte_insert_only_warns_complex():
    sql = "WITH c AS (SELECT id FROM t) INSERT INTO t2 SELECT * FROM c;"
    result = review_release_sql(sql)
    assert result["error_count"] == 0
    assert "complex_syntax" in [code for row in result["rows"] for code in row["rule"]]


def test_high_risk_warns():
    assert "high_risk" in rules("TRUNCATE TABLE t;")
    assert "high_risk" in rules("DROP DATABASE app_db;")
    assert "high_risk" not in rules("DROP TABLE tmp1;")


def test_mixed_ddl_dml_appends_warning_row():
    sql = "ALTER TABLE t ADD COLUMN c int; INSERT INTO t (c) VALUES (1);"
    result = review_release_sql(sql)
    assert "ddl_dml_mixed" in [code for row in result["rows"] for code in row["rule"]]
    assert result["warning_count"] == 1
    assert result["statement_count"] == 2


def test_comment_only_sql_reports_empty_error():
    result = review_release_sql("-- 只是注释")
    assert result["error_count"] == 1
    assert result["rows"][0]["rule"] == ["empty_sql"]


def test_empty_sql_reports_error():
    result = review_release_sql("   ;;  ")
    assert result["error_count"] == 1
    assert result["rows"][0]["rule"] == ["empty_sql"]


def test_multiple_statements_keep_per_statement_rows():
    sql = "BEGIN;\nUPDATE t SET a = 1;\nCOMMIT;"
    rows = review_release_sql(sql)["rows"]
    assert [row["id"] for row in rows] == [1, 2, 3]
    assert levels(sql) == [LEVEL_WARNING, LEVEL_ERROR, LEVEL_WARNING]
