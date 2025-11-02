from __future__ import annotations

from datetime import datetime
import math
from typing import Any
import unittest

from apscheduler.schedulers.blocking import BlockingScheduler
from freezegun import freeze_time
import pytest
from saic_ismart_client_ng.api.vehicle.schema import VinInfo

from configuration import Configuration
from integrations.openwb import ChargingStation, OpenWBIntegration
from tests.common_mocks import (
    DRIVETRAIN_RANGE_BMS,
    DRIVETRAIN_RANGE_VEHICLE,
    DRIVETRAIN_SOC_BMS,
    DRIVETRAIN_SOC_VEHICLE,
    VIN,
    get_mock_charge_management_data_resp,
    get_mock_vehicle_status_resp,
)
from tests.mocks import MessageCapturingConsolePublisher
from vehicle import VehicleState
from vehicle_info import VehicleInfo

RANGE_TOPIC = "/mock/range"
CHARGE_STATE_TOPIC = "/mock/charge/state"
SOC_TOPIC = "/mock/soc/state"
SOC_TS_TOPIC = "/mock/soc/timestamp"
CHARGING_VALUE = "VehicleIsCharging"
IMPORTED_ENERGY_TOPIC = "/mock/imported_energy"
CAR_CAPACITY_KWH = 50.0
CHARGE_POLLING_MIN_PERCENT = 5.0
ENERGY_PER_PERCENT = CAR_CAPACITY_KWH * 1000.0 / 100.0
ENERGY_FOR_MIN_PCT = math.ceil(CHARGE_POLLING_MIN_PERCENT * ENERGY_PER_PERCENT)


class TestOpenWBIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        config = Configuration()
        config.anonymized_publishing = False
        self.publisher = MessageCapturingConsolePublisher(config)
        vin_info = VinInfo()
        vin_info.vin = VIN
        vehicle_info = VehicleInfo(vin_info, None)
        account_prefix = f"/vehicles/{VIN}"
        scheduler = BlockingScheduler()
        self.vehicle_state = VehicleState(
            self.publisher, scheduler, account_prefix, vehicle_info
        )
        charging_station = ChargingStation(
            vin=VIN,
            charge_state_topic=CHARGE_STATE_TOPIC,
            charging_value=CHARGING_VALUE,
            soc_topic=SOC_TOPIC,
            soc_ts_topic=SOC_TS_TOPIC,
            range_topic=RANGE_TOPIC,
        )
        self.openwb_integration = OpenWBIntegration(
            charging_station=charging_station,
            publisher=self.publisher,
        )

    @freeze_time("2025-01-01 12:00:00")
    async def test_update_soc_with_no_bms_data(self) -> None:
        vehicle_status_resp = get_mock_vehicle_status_resp()
        result = self.vehicle_state.handle_vehicle_status(vehicle_status_resp)

        # Reset topics since we are only asserting the differences
        self.publisher.map.clear()

        self.openwb_integration.update_openwb(vehicle_status=result, charge_status=None)
        self.assert_mqtt_topic(
            SOC_TOPIC,
            float(DRIVETRAIN_SOC_VEHICLE),
        )
        self.assert_mqtt_topic(
            SOC_TS_TOPIC, int(datetime(2025, 1, 1, 12, 0, 0).timestamp())
        )
        self.assert_mqtt_topic(
            RANGE_TOPIC,
            DRIVETRAIN_RANGE_VEHICLE,
        )
        expected_topics = {
            SOC_TOPIC,
            SOC_TS_TOPIC,
            RANGE_TOPIC,
        }
        assert expected_topics == set(self.publisher.map.keys())

    @freeze_time("2025-01-01 12:00:00")
    async def test_update_soc_with_bms_data(self) -> None:
        vehicle_status_resp = get_mock_vehicle_status_resp()
        chrg_mgmt_data_resp = get_mock_charge_management_data_resp()
        vehicle_status_resp_result = self.vehicle_state.handle_vehicle_status(
            vehicle_status_resp
        )
        chrg_mgmt_data_resp_result = self.vehicle_state.handle_charge_status(
            chrg_mgmt_data_resp
        )

        # Reset topics since we are only asserting the differences
        self.publisher.map.clear()

        self.openwb_integration.update_openwb(
            vehicle_status=vehicle_status_resp_result,
            charge_status=chrg_mgmt_data_resp_result,
        )

        self.assert_mqtt_topic(SOC_TOPIC, DRIVETRAIN_SOC_BMS)
        self.assert_mqtt_topic(
            RANGE_TOPIC,
            DRIVETRAIN_RANGE_BMS,
        )
        self.assert_mqtt_topic(
            SOC_TS_TOPIC, int(datetime(2025, 1, 1, 12, 0, 0).timestamp())
        )
        expected_topics = {
            SOC_TOPIC,
            SOC_TS_TOPIC,
            RANGE_TOPIC,
        }
        assert expected_topics == set(self.publisher.map.keys())

    async def test_imported_energy_initial_threshold_set(self) -> None:
        """Initial call should set the first threshold but not trigger a refresh."""
        imported_energy = 1000.0  # 1 kWh

        self.openwb_integration.real_total_battery_capacity = CAR_CAPACITY_KWH
        self.openwb_integration.computed_refresh_by_imported_energy_wh = None

        should_refresh = self.openwb_integration.should_refresh_by_imported_energy(
            imported_energy, CHARGE_POLLING_MIN_PERCENT, VIN
        )

        assert not should_refresh, "Initial call should not trigger refresh"
        assert (
            self.openwb_integration.computed_refresh_by_imported_energy_wh is not None
        ), "Initial threshold should be computed and stored"

    async def test_imported_energy_threshold_reached_triggers_refresh(self) -> None:
        """When imported energy increases beyond threshold, refresh should trigger."""
        imported_energy_start = 1000.0  # 1 kWh
        threshold_wh = imported_energy_start + ENERGY_FOR_MIN_PCT

        self.openwb_integration.real_total_battery_capacity = CAR_CAPACITY_KWH
        self.openwb_integration.computed_refresh_by_imported_energy_wh = threshold_wh

        should_refresh = self.openwb_integration.should_refresh_by_imported_energy(
            threshold_wh, CHARGE_POLLING_MIN_PERCENT, VIN
        )

        assert should_refresh, "Refresh should trigger when threshold is reached"

    async def test_imported_energy_no_refresh_before_threshold(self) -> None:
        """Should not trigger refresh before threshold is reached."""
        imported_energy_wh = 1000.0  # 1 kWh
        threshold_wh = imported_energy_wh + ENERGY_FOR_MIN_PCT

        self.openwb_integration.real_total_battery_capacity = CAR_CAPACITY_KWH
        self.openwb_integration.computed_refresh_by_imported_energy_wh = threshold_wh

        should_refresh = self.openwb_integration.should_refresh_by_imported_energy(
            imported_energy_wh + (ENERGY_FOR_MIN_PCT / 2),
            CHARGE_POLLING_MIN_PERCENT,
            VIN,
        )

        assert not should_refresh, (
            "Should not refresh before threshold is fully reached"
        )

    async def test_imported_energy_updates_next_threshold(self) -> None:
        """After refresh, a new threshold should be calculated."""
        imported_energy_wh = 1000.0  # 1 kWh

        self.openwb_integration.real_total_battery_capacity = CAR_CAPACITY_KWH
        self.openwb_integration.computed_refresh_by_imported_energy_wh = imported_energy_wh

        should_refresh = self.openwb_integration.should_refresh_by_imported_energy(
            imported_energy_wh + ENERGY_FOR_MIN_PCT,
            CHARGE_POLLING_MIN_PERCENT,
            VIN,
        )

        assert should_refresh, "Refresh should be triggered at the threshold"
        assert (
            self.openwb_integration.computed_refresh_by_imported_energy_wh
            == imported_energy_wh + ENERGY_FOR_MIN_PCT * 2
        ), "Next threshold should be correctly updated"

    async def test_imported_energy_missing_battery_capacity(self) -> None:
        """If no battery capacity is known, refresh calculation should be skipped."""
        self.openwb_integration.real_total_battery_capacity = None

        result = self.openwb_integration.should_refresh_by_imported_energy(
            imported_energy_wh=1000.0,
            charge_polling_min_percent=CHARGE_POLLING_MIN_PERCENT,
            vin=VIN,
        )

        assert not result, "Should not refresh if capacity is unknown"

    async def test_imported_energy_charger_disconnected(self) -> None:
        """If charger is disconnected, refresh calculation should be skipped."""
        self.openwb_integration.charger_connected = False

        should_refresh = self.openwb_integration.should_refresh_by_imported_energy(
            imported_energy_wh=1000.0,
            charge_polling_min_percent=CHARGE_POLLING_MIN_PERCENT,
            vin=VIN,
        )

        assert not should_refresh, "Should not refresh if charger is disconnected"

    async def test_imported_energy_calculation_reset_refresh(self) -> None:
        """Test that the refresh threshold is recalculated if imported_energy_wh decreases, simulating a daily reset or counter rollover."""
        imported_energy_wh = 1000.0  # 1 kWh

        self.openwb_integration.real_total_battery_capacity = CAR_CAPACITY_KWH

        # Initial energy value
        should_refresh = self.openwb_integration.should_refresh_by_imported_energy(
            imported_energy_wh, CHARGE_POLLING_MIN_PERCENT, VIN
        )
        assert not should_refresh, "Initial call should not trigger refresh"

        # Increase energy above threshold
        should_refresh = self.openwb_integration.should_refresh_by_imported_energy(
            imported_energy_wh + ENERGY_FOR_MIN_PCT,
            CHARGE_POLLING_MIN_PERCENT,
            VIN,
        )
        assert should_refresh, "Refresh should trigger when threshold is reached"

        # Simulate daily reset to 0 Wh
        reset_energy_wh = 0.0
        # Should NOT trigger refresh immediately
        should_refresh = self.openwb_integration.should_refresh_by_imported_energy(
            reset_energy_wh, CHARGE_POLLING_MIN_PERCENT, VIN
        )
        assert not should_refresh, "Reset should not trigger refresh"

        # Next increase above new threshold
        should_refresh = self.openwb_integration.should_refresh_by_imported_energy(
            reset_energy_wh + ENERGY_FOR_MIN_PCT,
            CHARGE_POLLING_MIN_PERCENT,
            VIN,
        )
        assert should_refresh, "Refresh should trigger after reset and threshold reached"


    def assert_mqtt_topic(self, topic: str, value: Any) -> None:
        mqtt_map = self.publisher.map
        if topic in mqtt_map:
            if isinstance(value, float) or isinstance(mqtt_map[topic], float):
                assert value == pytest.approx(mqtt_map[topic], abs=0.1)
            else:
                assert value == mqtt_map[topic]
        else:
            self.fail(f"MQTT map does not contain topic {topic}")
