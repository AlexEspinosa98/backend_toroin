"""Cortes de pago congelados y préstamos del trabajador

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'prestamos',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('empresa_id', sa.String(length=36), nullable=False),
        sa.Column('trabajador_id', sa.String(length=36), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('concepto', sa.String(length=200), server_default='', nullable=False),
        sa.Column('valor', sa.Float(), server_default='0', nullable=False),
        sa.Column('cuota', sa.Float(), server_default='0', nullable=False),
        sa.Column('saldo', sa.Float(), server_default='0', nullable=False),
        sa.Column('activo', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('notas', sa.String(length=300), server_default='', nullable=False),
        sa.Column('creado', sa.DateTime(timezone=True), nullable=False),
        sa.Column('creado_por', sa.String(length=200), server_default='', nullable=False),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        sa.ForeignKeyConstraint(['trabajador_id'], ['trabajadores.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_prestamos_empresa_id'), 'prestamos', ['empresa_id'])
    op.create_index(op.f('ix_prestamos_trabajador_id'), 'prestamos', ['trabajador_id'])

    op.create_table(
        'cortes',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('empresa_id', sa.String(length=36), nullable=False),
        sa.Column('obra_id', sa.String(length=36), server_default='', nullable=False),
        sa.Column('desde', sa.Date(), nullable=False),
        sa.Column('hasta', sa.Date(), nullable=False),
        sa.Column('estado', sa.String(length=10), server_default='abierto', nullable=False),
        sa.Column('notas', sa.String(length=300), server_default='', nullable=False),
        sa.Column('totales', sa.JSON(), nullable=False),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('creado', sa.DateTime(timezone=True), nullable=False),
        sa.Column('creado_por', sa.String(length=200), server_default='', nullable=False),
        sa.Column('cerrado_en', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cerrado_por', sa.String(length=200), server_default='', nullable=False),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_cortes_empresa_id'), 'cortes', ['empresa_id'])
    op.create_index(op.f('ix_cortes_obra_id'), 'cortes', ['obra_id'])

    op.create_table(
        'corte_filas',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('empresa_id', sa.String(length=36), nullable=False),
        sa.Column('corte_id', sa.String(length=36), nullable=False),
        sa.Column('trabajador_id', sa.String(length=36), nullable=False),
        sa.Column('nombre', sa.String(length=200), server_default='', nullable=False),
        sa.Column('cedula', sa.String(length=30), server_default='', nullable=False),
        sa.Column('cargo', sa.String(length=80), server_default='', nullable=False),
        sa.Column('modalidad', sa.String(length=10), server_default='jornal', nullable=False),
        sa.Column('dias_asistidos', sa.Integer(), server_default='0', nullable=False),
        sa.Column('dias_descanso', sa.Integer(), server_default='0', nullable=False),
        sa.Column('devengado', sa.Float(), server_default='0', nullable=False),
        sa.Column('auxilio', sa.Float(), server_default='0', nullable=False),
        sa.Column('salud', sa.Float(), server_default='0', nullable=False),
        sa.Column('pension', sa.Float(), server_default='0', nullable=False),
        sa.Column('prestamo', sa.Float(), server_default='0', nullable=False),
        sa.Column('otras', sa.Float(), server_default='0', nullable=False),
        sa.Column('neto', sa.Float(), server_default='0', nullable=False),
        sa.Column('nota', sa.String(length=200), server_default='', nullable=False),
        sa.Column('detalle', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        sa.ForeignKeyConstraint(['corte_id'], ['cortes.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_corte_filas_empresa_id'), 'corte_filas', ['empresa_id'])
    op.create_index(op.f('ix_corte_filas_corte_id'), 'corte_filas', ['corte_id'])
    op.create_index(op.f('ix_corte_filas_trabajador_id'), 'corte_filas', ['trabajador_id'])

    op.create_table(
        'abonos_prestamo',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('empresa_id', sa.String(length=36), nullable=False),
        sa.Column('prestamo_id', sa.String(length=36), nullable=False),
        sa.Column('corte_id', sa.String(length=36), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('valor', sa.Float(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        sa.ForeignKeyConstraint(['prestamo_id'], ['prestamos.id'], ),
        sa.ForeignKeyConstraint(['corte_id'], ['cortes.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_abonos_prestamo_empresa_id'), 'abonos_prestamo', ['empresa_id'])
    op.create_index(op.f('ix_abonos_prestamo_prestamo_id'), 'abonos_prestamo', ['prestamo_id'])
    op.create_index(op.f('ix_abonos_prestamo_corte_id'), 'abonos_prestamo', ['corte_id'])


def downgrade() -> None:
    op.drop_table('abonos_prestamo')
    op.drop_table('corte_filas')
    op.drop_table('cortes')
    op.drop_table('prestamos')
