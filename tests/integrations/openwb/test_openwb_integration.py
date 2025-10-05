from __future__ import annotations

from typing import Any
import unittest

from apscheduler.schedulers.blocking import BlockingScheduler
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
        )
        charging_station.range_topic = RANGE_TOPIC
        self.openwb_integration = OpenWBIntegration(
            charging_station=charging_station,
            publisher=self.publisher,
        )

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
            SOC_TS_TOPIC,
            int,  # We just check that it's an int, the exact value is time-dependent
        )
        self.assert_mqtt_topic(
            RANGE_TOPIC,
            DRIVETRAIN_RANGE_VEHICLE,
        )
        expected_topics = {
            SOC_TOPIC,
            RANGE_TOPIC,
        }
        assert expected_topics == set(self.publisher.map.keys())

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
        expected_topics = {
            SOC_TOPIC,
            RANGE_TOPIC,
        }
        assert expected_topics == set(self.publisher.map.keys())

    def assert_mqtt_topic(self, topic: str, value: Any) -> None:
        mqtt_map = self.publisher.map
        if topic in mqtt_map:
            if isinstance(value, float) or isinstance(mqtt_map[topic], float):
                assert value == pytest.approx(mqtt_map[topic], abs=0.1)
            else:
                assert value == mqtt_map[topic]
        else:
            self.fail(f"MQTT map does not contain topic {topic}")
