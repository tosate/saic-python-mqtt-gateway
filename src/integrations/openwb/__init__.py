from __future__ import annotations

import datetime
import logging
import math
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
        self.charger_connected: bool | None = None
        self.real_total_battery_capacity: float | None = None
        self.last_imported_energy_wh: float | None = None
        self.computed_refresh_by_imported_energy_wh: float | None = None

    def update_openwb(
        self,
        vehicle_status: VehicleStatusRespProcessingResult,
        charge_status: ChrgMgmtDataRespProcessingResult | None,
    ) -> None:
        if charge_status:
            self.real_total_battery_capacity = charge_status.real_total_battery_capacity
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

        soc_ts_topic = self.__charging_station.soc_ts_topic
        soc_ts = int(datetime.datetime.now().timestamp())
        if soc_ts_topic is not None:
            LOG.info("OpenWB Integration published SoC timestamp to %s", soc_ts_topic)
            self.__publisher.publish_int(
                key=soc_ts_topic,
                value=soc_ts,
                no_prefix=True,
            )

    def set_charger_connection_state(self, connected: bool, vin: str) -> None:
        if self.charger_connected == connected:
            # No change, do nothing
            return

        self.charger_connected = connected
        if self.charger_connected:
            LOG.info("OpenWB Integration: Charger connected to vehicle %s", vin)
        else:
            LOG.info("OpenWB Integration: Charger disconnected from vehicle %s", vin)

    def should_refresh_by_imported_energy(
        self, imported_energy_wh: float, charge_polling_min_percent: float, vin: str
    ) -> bool:
        """Determine if the vehicle status should be refreshed based on imported energy.

        The method triggers a refresh if the imported energy since the last refresh
        exceeds a threshold based on battery capacity and a minimum percentage.
        If the imported energy decreases (e.g., daily reset), the threshold is recalculated.
        """
        # Charger connection state might not be available. If disconnected, skip the check.
        if self.charger_connected is False:
            LOG.debug(
                f"Charger for vehicle {vin} is disconnected. Skipping imported energy check."
            )
            return False
        # Return False if battery capacity is not available
        if self.real_total_battery_capacity is None:
            LOG.warning(
                "Battery capacity not available. Cannot calculate energy per percent."
            )
            return False

        # Calculate the energy corresponding to 1% of the battery in Wh
        energy_per_percent = (self.real_total_battery_capacity * 1000) / 100.0

        # Minimum energy threshold for triggering a refresh
        energy_for_min_pct = math.ceil(charge_polling_min_percent * energy_per_percent)

        # Initialize the refresh threshold if it hasn't been set yet
        if not self.computed_refresh_by_imported_energy_wh or imported_energy_wh < (
            self.last_imported_energy_wh or 0
        ):
            self.computed_refresh_by_imported_energy_wh = (
                imported_energy_wh + energy_for_min_pct
            )
            LOG.debug(
                f"Initial or reset threshold for vehicle {vin} set to "
                f"{self.computed_refresh_by_imported_energy_wh} Wh"
            )

        # Check if the imported energy exceeds the threshold
        refresh_needed = False
        if imported_energy_wh >= self.computed_refresh_by_imported_energy_wh:
            LOG.info(
                f"OpenWB Integration: Imported energy threshold of {self.computed_refresh_by_imported_energy_wh} Wh reached "
                f"(current: {imported_energy_wh} Wh). Triggering vehicle refresh."
            )
            refresh_needed = True

            # Calculate the next threshold
            self.computed_refresh_by_imported_energy_wh = (
                imported_energy_wh + energy_for_min_pct
            )
            LOG.debug(
                f"Next imported energy threshold for vehicle {vin} set to "
                f"{self.computed_refresh_by_imported_energy_wh} Wh"
            )

        # Save the last imported energy value
        self.last_imported_energy_wh = imported_energy_wh
        return refresh_needed
