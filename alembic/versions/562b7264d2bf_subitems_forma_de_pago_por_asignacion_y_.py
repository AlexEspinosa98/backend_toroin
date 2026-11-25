"""subitems, forma de pago por asignacion y rubro en gastos

Revision ID: 562b7264d2bf
Revises: e5f6a7b8c9d0
Create Date: 2026-11-25 08:58:02.893340

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '562b7264d2bf'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('actividades', sa.Column('padre_id', sa.String(length=36), nullable=False, server_default=''))
    op.add_column('actividades', sa.Column('equivale', sa.Float(), nullable=False, server_default='0'))
    op.add_column('actividades', sa.Column('aporta_avance', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_index(op.f('ix_actividades_padre_id'), 'actividades', ['padre_id'], unique=False)
    op.add_column('asignaciones', sa.Column('modalidad', sa.String(length=10), nullable=False, server_default=''))
    op.add_column('asignaciones', sa.Column('jornal', sa.Float(), nullable=True))
    op.add_column('facturas', sa.Column('actividad_id', sa.String(length=36), nullable=False, server_default=''))
    op.create_index(op.f('ix_facturas_actividad_id'), 'facturas', ['actividad_id'], unique=False)
    op.add_column('gastos', sa.Column('actividad_id', sa.String(length=36), nullable=False, server_default=''))
    op.create_index(op.f('ix_gastos_actividad_id'), 'gastos', ['actividad_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_gastos_actividad_id'), table_name='gastos')
    op.drop_column('gastos', 'actividad_id')
    op.drop_index(op.f('ix_facturas_actividad_id'), table_name='facturas')
    op.drop_column('facturas', 'actividad_id')
    op.drop_column('asignaciones', 'jornal')
    op.drop_column('asignaciones', 'modalidad')
    op.drop_index(op.f('ix_actividades_padre_id'), table_name='actividades')
    op.drop_column('actividades', 'aporta_avance')
    op.drop_column('actividades', 'equivale')
    op.drop_column('actividades', 'padre_id')
