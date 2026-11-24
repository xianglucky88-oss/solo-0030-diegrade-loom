"""Compatibility facade for station route planning and work-order dispatch."""

from .stations import register as register_station
from .stations import register_any as register_stations
from .workorders import (
    choose_route,
    complete,
    dispatch,
    get_order,
    list_orders,
    locks_for_wafer,
    plan,
)

create_order = dispatch
dispatch_order = dispatch
complete_order = complete
submit_order = complete
register_dispatch_station = register_station

__all__ = [
    'choose_route', 'complete', 'complete_order', 'create_order', 'dispatch',
    'dispatch_order', 'get_order', 'list_orders', 'locks_for_wafer', 'plan',
    'register_dispatch_station', 'register_station', 'register_stations',
    'submit_order',
]
