"""The Garmin Connect integration."""
from datetime import date
from datetime import timedelta
import logging
import asyncio
from collections.abc import Awaitable

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, IntegrationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DATA_COORDINATOR,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    GEAR,
    SERVICE_SETTING,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Garmin Connect from a config entry."""

    coordinator = GarminConnectDataUpdateCoordinator(hass, entry=entry)

    if not await coordinator.async_login():
        return False

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {DATA_COORDINATOR: coordinator}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class GarminConnectDataUpdateCoordinator(DataUpdateCoordinator):
    """Garmin Connect Data Update Coordinator."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the Garmin Connect hub."""
        self.entry = entry
        self.hass = hass
        self.in_china = False

        country = self.hass.config.country
        if country == "CN":
            self.in_china = True

        self._api = Garmin(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD], self.in_china)

        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_UPDATE_INTERVAL
        )

    async def async_login(self) -> bool:
        """Login to Garmin Connect."""
        try:
            await self.hass.async_add_executor_job(self._api.login)
        except (
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
        ) as err:
            _LOGGER.error("Error occurred during Garmin Connect login request: %s", err)
            return False
        except (GarminConnectConnectionError) as err:
            _LOGGER.error(
                "Connection error occurred during Garmin Connect login request: %s", err
            )
            raise ConfigEntryNotReady from err
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception(
                "Unknown error occurred during Garmin Connect login request"
            )
            return False

        return True

    async def _async_update_data(self) -> dict:
        """Fetch data from Garmin Connect."""

        summary = {}
        body = {}
        alarms = {}
        gear = {}
        gear_stats = {}
        gear_defaults = {}
        activity_types = {}
        sleep_data = {}
        sleep_score = None
        sleep_time_seconds = None
        hrv_data = {}
        hrvStatus = {"status": "UNKNOWN"}

        try:
            summary = await self.hass.async_add_executor_job(
                self._api.get_user_summary, date.today().isoformat()
            )
            _LOGGER.debug(f"Summary data: {summary}")

            body = await self.hass.async_add_executor_job(
                self._api.get_body_composition, date.today().isoformat()
            )
            _LOGGER.debug(f"Body data: {body}")

            activities = await self.hass.async_add_executor_job(
                self._api.get_activities_by_date, (date.today()-timedelta(days=7)).isoformat(), (date.today()+timedelta(days=1)).isoformat()
            )
            _LOGGER.debug(f"Activities data: {activities}")
            summary['lastActivities'] = activities

            badges = await self.hass.async_add_executor_job(
                self._api.get_earned_badges
            )
            _LOGGER.debug(f"Badges data: {badges}")
            summary['badges'] = badges

            alarms = await self.hass.async_add_executor_job(self._api.get_device_alarms)
            _LOGGER.debug(f"Alarms data: {alarms}")

            activity_types = await self.hass.async_add_executor_job(
                self._api.get_activity_types
            )
            _LOGGER.debug(f"Activity types data: {activity_types}")

            sleep_data = await self.hass.async_add_executor_job(
                self._api.get_sleep_data, date.today().isoformat())
            _LOGGER.debug(f"Sleep data: {sleep_data}")

            hrv_data = await self.hass.async_add_executor_job(
                self._api.get_hrv_data, date.today().isoformat())
            _LOGGER.debug(f"hrv data: {hrv_data}")
        except (
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
            GarminConnectConnectionError
        ) as error:
            _LOGGER.debug("Trying to relogin to Garmin Connect")
            if not await self.async_login():
                raise UpdateFailed(error) from error
            return {}

        try:
            gear = await self.hass.async_add_executor_job(
                self._api.get_gear, summary[GEAR.USERPROFILE_ID]
            )
            _LOGGER.debug(f"Gear data: {gear}")

            tasks: list[Awaitable] = [
                self.hass.async_add_executor_job(
                    self._api.get_gear_stats, gear_item[GEAR.UUID]
                )
                for gear_item in gear
            ]
            gear_stats = await asyncio.gather(*tasks)
            _LOGGER.debug(f"Gear stats data: {gear_stats}")

            gear_defaults = await self.hass.async_add_executor_job(
                self._api.get_gear_defaults, summary[GEAR.USERPROFILE_ID]
            )
            _LOGGER.debug(f"Gear defaults data: {gear_defaults}")
        except:
            _LOGGER.debug("Gear data is not available")

        try:
            sleep_score = sleep_data["dailySleepDTO"]["sleepScores"]["overall"]["value"]
            _LOGGER.debug(f"Sleep score data: {sleep_score}")
        except KeyError:
            _LOGGER.debug("Sleep score data is not available")

        try:
            sleep_time_seconds = sleep_data["dailySleepDTO"]["sleepTimeSeconds"]
            _LOGGER.debug(f"Sleep time seconds data: {sleep_time_seconds}")
        except KeyError:
            _LOGGER.debug("Sleep time seconds data is not available")

        try:
            if hrv_data and "hrvSummary" in hrv_data:
                hrvStatus = hrv_data["hrvSummary"]
                _LOGGER.debug(f"HRV status: {hrvStatus} ")
        except KeyError:
            _LOGGER.debug("HRV data is not available")

        return {
            **summary,
            **body["totalAverage"],
            "nextAlarm": alarms,
            "gear": gear,
            "gear_stats": gear_stats,
            "activity_types": activity_types,
            "gear_defaults": gear_defaults,
            "sleepScore": sleep_score,
            "sleepTimeSeconds": sleep_time_seconds,
            "hrvStatus": hrvStatus,
        }

    async def set_active_gear(self, entity, service_data):
        """Update Garmin Gear settings"""
        if not await self.async_login():
            raise IntegrationError(
                "Failed to login to Garmin Connect, unable to update"
            )

        setting = service_data.data["setting"]
        activity_type_id = next(
            filter(
                lambda a: a[GEAR.TYPE_KEY] == service_data.data["activity_type"],
                self.data["activity_types"],
            )
        )[GEAR.TYPE_ID]
        if setting != SERVICE_SETTING.ONLY_THIS_AS_DEFAULT:
            await self.hass.async_add_executor_job(
                self._api.set_gear_default,
                activity_type_id,
                entity.uuid,
                setting == SERVICE_SETTING.DEFAULT,
            )
        else:
            old_default_state = await self.hass.async_add_executor_job(
                self._api.get_gear_defaults, self.data[GEAR.USERPROFILE_ID]
            )
            to_deactivate = list(
                filter(
                    lambda o: o[GEAR.ACTIVITY_TYPE_PK] == activity_type_id
                    and o[GEAR.UUID] != entity.uuid,
                    old_default_state,
                )
            )

            for active_gear in to_deactivate:
                await self.hass.async_add_executor_job(
                    self._api.set_gear_default,
                    activity_type_id,
                    active_gear[GEAR.UUID],
                    False,
                )
            await self.hass.async_add_executor_job(
                self._api.set_gear_default, activity_type_id, entity.uuid, True
            )

    async def add_body_composition(self, entity, service_data):
        """Record a weigh in/body composition"""
        if not await self.async_login():
            raise IntegrationError(
                "Failed to login to Garmin Connect, unable to update"
            )

        await self.hass.async_add_executor_job(
            self._api.add_body_composition,
                    service_data.data.get("timestamp", None),
                    service_data.data.get("weight"),
                    service_data.data.get("percent_fat", None),
                    service_data.data.get("percent_hydration", None),
                    service_data.data.get("visceral_fat_mass", None),
                    service_data.data.get("bone_mass", None),
                    service_data.data.get("muscle_mass", None),
                    service_data.data.get("basal_met", None),
                    service_data.data.get("active_met", None),
                    service_data.data.get("physique_rating", None),
                    service_data.data.get("metabolic_age", None),
                    service_data.data.get("visceral_fat_rating", None),
                    service_data.data.get("bmi", None)
        )

    async def add_blood_pressure(self, entity, service_data):
        """Record a blood pressure measurement"""

        if not await self.async_login():
            raise IntegrationError(
                "Failed to login to Garmin Connect, unable to update"
            )

        await self.hass.async_add_executor_job(
            self._api.set_blood_pressure,
                    service_data.data.get('systolic'),
                    service_data.data.get('diastolic'),
                    service_data.data.get('pulse'),
                    service_data.data.get('note', None)
        )
