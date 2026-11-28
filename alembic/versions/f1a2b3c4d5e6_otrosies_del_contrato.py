"""Otrosíes del contrato: adiciones, prórrogas e ítems nuevos

Revision ID: f1a2b3c4d5e6
Revises: 562b7264d2bf
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = '562b7264d2bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'otrosies',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('empresa_id', sa.String(length=36), nullable=False),
        sa.Column('obra_id', sa.String(length=36), nullable=False),
        sa.Column('numero', sa.String(length=60), server_default='', nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('descripcion', sa.Text(), server_default='', nullable=False),
        sa.Column('valor_adicion', sa.Float(), server_default='0', nullable=False),
        sa.Column('fecha_fin_nueva', sa.Date(), nullable=True),
        sa.Column('archivo_nombre', sa.String(length=200), server_default='', nullable=False),
        sa.Column('archivo', sa.Text(), server_default='', nullable=False),
        sa.Column('archivo_bytes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('creado', sa.DateTime(timezone=True), nullable=False),
        sa.Column('creado_por', sa.String(length=200), server_default='', nullable=False),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        sa.ForeignKeyConstraint(['obra_id'], ['obras.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_otrosies_empresa_id'), 'otrosies', ['empresa_id'])
    op.create_index(op.f('ix_otrosies_obra_id'), 'otrosies', ['obra_id'])

    op.add_column('actividades', sa.Column('otrosi_id', sa.String(length=36), server_default='', nullable=False))
    op.create_index(op.f('ix_actividades_otrosi_id'), 'actividades', ['otrosi_id'])

    op.add_column('contratos', sa.Column('fecha_fin_base', sa.Date(), nullable=True))
    op.add_column('contratos', sa.Column('valor_base', sa.Float(), server_default='0', nullable=False))
    # Los contratos que ya existen no tienen otrosíes: su valor y su plazo vigentes son los del contrato original.
    op.execute("UPDATE contratos SET valor_base = valor_total, fecha_fin_base = fecha_fin")


def downgrade() -> None:
    op.drop_column('contratos', 'valor_base')
    op.drop_column('contratos', 'fecha_fin_base')
    op.drop_index(op.f('ix_actividades_otrosi_id'), table_name='actividades')
    op.drop_column('actividades', 'otrosi_id')
    op.drop_index(op.f('ix_otrosies_obra_id'), table_name='otrosies')
    op.drop_index(op.f('ix_otrosies_empresa_id'), table_name='otrosies')
    op.drop_table('otrosies')
