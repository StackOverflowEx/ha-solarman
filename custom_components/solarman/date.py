from __future__ import annotations

import logging

from datetime import date, datetime # type: ignore

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.date import DateEntity, DateEntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import *
from .common import *
from .services import *
from .entity import create_entity, SolarmanWritableEntity

_LOGGER = logging.getLogger(__name__)

_PLATFORM = get_current_file_name(__name__)

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> bool:
    _LOGGER.debug(f"async_setup_entry: {config_entry.options}")

    coordinator, descriptions = get_coordinator_descriptions(hass, config_entry.entry_id, _PLATFORM)

    _LOGGER.debug(f"async_setup_entry: async_add_entities: {descriptions}")

    async_add_entities(create_entity(lambda x: SolarmanDateEntity(coordinator, x), d) for d in descriptions)

    return True

async def async_unload_entry(_: HomeAssistant, config_entry: ConfigEntry) -> bool:
    _LOGGER.debug(f"async_unload_entry: {config_entry.options}")

    return True

class SolarmanDateEntity(SolarmanWritableEntity, DateEntity):
    def __init__(self, coordinator, sensor):
        SolarmanWritableEntity.__init__(self, coordinator, sensor)

        self._multiple_registers = len(self.registers) > 1 and self.registers[1] == self.registers[0] + 1
        self._hex = "hex" in sensor
        self._d = (100 if not "dec" in sensor else sensor["dec"]) if not self._hex else (0x100 if sensor["hex"] is None else sensor["hex"])
        self._offset = sensor["offset"] if "offset" in sensor else None

    def _to_native_value(self, value: date) -> int | list:
        if self._hex:
            if self._multiple_registers and self._offset and self._offset >= 0x100:
                return [concat_hex(div_mod(value.month, 10)) + self._offset, concat_hex(div_mod(value.day, 10)) + self._offset]
            return concat_hex((value.month, value.day))
        return value.month * self._d + value.day if not self._multiple_registers else [value.month, value.day]

    @property
    def native_value(self) -> date | None:
        """Return the state of the setting entity."""
        try:
            if self._attr_native_value:
                d = None
                if isinstance(self._attr_native_value, list) and len(self._attr_native_value) > 1:
                    d = datetime.strptime(f"{self._attr_native_value[0]}:{self._attr_native_value[1]}:{curr_year}", DATE_FORMAT).date()
                d = datetime.strptime(self._attr_native_value, DATE_FORMAT).date()
                #Set year to current
                curr_year = datetime.now().year
                return date(curr_year, d.month, d.day)
        except Exception as e:
            _LOGGER.debug(f"SolarmanDateEntity.native_value of {self._attr_name}: {format_exception(e)}")
        return None

    async def async_set_value(self, value: date) -> None:
        """Change the date."""
        await self.write(self._to_native_value(value), value.strftime(DATE_FORMAT))
