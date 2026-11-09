"""Overture Maps 공개 S3(익명) 접속 — 저장소 안에서 독립 실행용(프록시·CA 는 있으면 씀)."""
import duckdb, os
REL = os.environ.get('OVERTURE_RELEASE', 'release/2026-10-21.0')
SRC = f"read_parquet('s3://overturemaps-us-west-2/{REL}/theme=places/type=place/*', hive_partitioning=1)"


def con():
    c = duckdb.connect()
    c.execute('INSTALL httpfs; LOAD httpfs;')
    if os.environ.get('HTTPS_PROXY'):
        c.execute(f"SET http_proxy='{os.environ['HTTPS_PROXY'].replace('http://', '')}'")
    if os.path.exists('/root/.ccr/ca-bundle.crt'):
        c.execute("SET ca_cert_file='/root/.ccr/ca-bundle.crt'")
    c.execute("SET s3_region='us-west-2'")
    return c
