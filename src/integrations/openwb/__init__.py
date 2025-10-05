from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import extractors
from integrations.openwb.charging_station import ChargingStation

if TYPE_CHECKING:
    from publisher.core import Publisher
    from status_publisher.charge.chrg_mgmt_data_resp import (
        ChrgMgmtDataRespProcessingResult,
    )
    from status_publisher.vehicle.vehicle_status_resp import (
        VehicleStatusRespProcessingResult,
    )

LOG = logging.getLogger(__name__)

__all__ = [
    "ChargingStation",
    "OpenWBIntegration",
]


class OpenWBIntegration:
    def __init__(
        self, *, charging_station: ChargingStation, publisher: Publisher
    ) -> None:
        self.__charging_station = charging_station
        self.__publisher = publisher

    def update_openwb(
        self,
        vehicle_status: VehicleStatusRespProcessingResult,
        charge_status: ChrgMgmtDataRespProcessingResult | None,
    ) -> None:
        range_topic = self.__charging_station.range_topic
        electric_range = extractors.extract_electric_range(
            vehicle_status, charge_status
        )
        if electric_range is not None and range_topic is not None:
            LOG.info("OpenWB Integration published range to %s", range_topic)
            self.__publisher.publish_float(
                key=range_topic,
                value=electric_range,
                no_prefix=True,
            )

        soc_topic = self.__charging_station.soc_topic
        soc = extractors.extract_soc(vehicle_status, charge_status)
        if soc is not None and soc_topic is not None:
            LOG.info("OpenWB Integration published SoC to %s", soc_topic)
            self.__publisher.publish_float(
                key=soc_topic,
                value=soc,
                no_prefix=True,
            )
