"""Pytest fixtures for SoundBridge tests."""
import pytest
import os
import tempfile
import sqlite3


@pytest.fixture
def mock_shazam_db(tmp_path):
    """Create a mock Shazam SQLite database for testing."""
    db_path = tmp_path / "ShazamDataModel.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE ZSHTAGRESULTMO (
            Z_PK INTEGER PRIMARY KEY,
            ZTRACKNAME TEXT,
            ZDATE REAL
        );
        CREATE TABLE ZSHARTISTMO (
            Z_PK INTEGER PRIMARY KEY,
            ZNAME TEXT,
            ZTAGRESULT INTEGER,
            FOREIGN KEY (ZTAGRESULT) REFERENCES ZSHTAGRESULTMO(Z_PK)
        );
        INSERT INTO ZSHTAGRESULTMO (Z_PK, ZTRACKNAME, ZDATE) VALUES
            (1, 'Prisoner Song (Extended Original Mix)', 1000000),
            (2, 'Bring Me Da Ruckus', 1000001),
            (3, 'Real Muthaphuckkin''s G''s', 1000002);
        INSERT INTO ZSHARTISTMO (Z_PK, ZNAME, ZTAGRESULT) VALUES
            (1, 'Nova Nova', 1),
            (2, 'Wu Tang Clan', 2),
            (3, 'Eazy-E', 3);
    """)
    conn.commit()
    conn.close()
    return str(db_path)
