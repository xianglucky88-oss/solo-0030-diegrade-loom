from .workorders import *  # noqa: F401,F403
from .workorders import complete, dispatch, get_order, list_orders

create_work_order = dispatch
complete_work_order = complete

__all__ = ['dispatch', 'complete', 'get_order', 'list_orders',
           'create_work_order', 'complete_work_order']
