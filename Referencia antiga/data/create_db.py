#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TRIBUNAL GOLDENMASTER - Script de Criação da Base de Dados
============================================================
Cria/reinicializa a base de dados SQLite a partir do schema.sql

Uso:
    python data/create_db.py
    python data/create_db.py --force  # Apaga BD existente
"""

import sqlite3
import sys
from pathlib import Path


def create_database(db_path: Path, schema_path: Path, force: bool = False):
    """
    Cria a base de dados a partir do schema SQL.

    Args:
        db_path: Caminho para o ficheiro .db
        schema_path: Caminho para o ficheiro schema.sql
        force: Se True, apaga BD existente
    """
    # Verificar schema
    if not schema_path.exists():
        print(f"ERRO: Schema não encontrado: {schema_path}")
        sys.exit(1)

    # Se BD existe e não é force, avisar
    if db_path.exists():
        if force:
            print(f"Apagando BD existente: {db_path}")
            db_path.unlink()
        else:
            print(f"AVISO: BD já existe: {db_path}")
            print("Use --force para recriar")
            response = input("Continuar mesmo assim? (s/N): ")
            if response.lower() != 's':
                print("Abortado.")
                sys.exit(0)

    # Criar diretório se necessário
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Ler schema
    print(f"Lendo schema: {schema_path}")
    with open(schema_path, 'r', encoding='utf-8') as f:
        schema_sql = f.read()

    # Criar BD e executar schema
    print(f"Criando BD: {db_path}")
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    try:
        cursor.executescript(schema_sql)
        conn.commit()
        print("Schema executado com sucesso!")

        # Verificar tabelas criadas
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print(f"\nTabelas criadas: {len(tables)}")
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table[0]}")
            count = cursor.fetchone()[0]
            print(f"  - {table[0]}: {count} registos")

    except sqlite3.Error as e:
        print(f"ERRO ao executar schema: {e}")
        conn.rollback()
        sys.exit(1)

    finally:
        conn.close()

    print(f"\nBD criada com sucesso: {db_path}")


def main():
    """Função principal."""
    # Determinar caminhos
    script_dir = Path(__file__).resolve().parent
    db_path = script_dir / "legislacao_pt.db"
    schema_path = script_dir / "schema.sql"

    # Verificar flag --force
    force = "--force" in sys.argv or "-f" in sys.argv

    print("=" * 60)
    print("TRIBUNAL GOLDENMASTER - Criação da Base de Dados")
    print("=" * 60)

    create_database(db_path, schema_path, force)


if __name__ == "__main__":
    main()
