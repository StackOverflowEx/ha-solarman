from __future__ import annotations

import logging

from typing import Any
from decimal import Decimal
from datetime import date, datetime, time

from homeassistant.util import slugify
from homeassistant.core import split_entity_id, callback
from homeassistant.const import EntityCategory, STATE_UNKNOWN, CONF_FRIENDLY_NAME
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_registry import RegistryEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.typing import UNDEFINED, StateType, UndefinedType

from .const import *
from .common import *
from .services import *
from .coordinator import InverterCoordinator

_LOGGER = logging.getLogger(__name__)

@callback
def migrate_unique_ids(name: str, serial: int, entity_entry: RegistryEntry) -> dict[str, Any] | None:

    entity_name = entity_entry.original_name if entity_entry.has_entity_name or not entity_entry.original_name else entity_entry.original_name.replace(name, '').strip()
    old_unique_id = '_'.join(filter(None, (name, str(serial), entity_name)))
    slugified_old_unique_id = slugify(old_unique_id)

    for old_unique_id in (old_unique_id, slugified_old_unique_id):
        if entity_entry.unique_id == old_unique_id and (new_unique_id := f"{slugified_old_unique_id}_{split_entity_id(entity_entry.entity_id)[0]}"):
            _LOGGER.debug("Migrating unique_id for %s entity from [%s] to [%s]", entity_entry.entity_id, old_unique_id, new_unique_id)
            return { "new_unique_id": entity_entry.unique_id.replace(old_unique_id, new_unique_id) }

    return None

def create_entity(creator, description):
    try:
        entity = creator(description)

        if description is not None and (nlookup := description.get("name_lookup")) is not None and (prefix := entity.coordinator.data.get(nlookup)) is not None:
            description["name"] = replace_first(description["name"], get_tuple(prefix))
            description["key"] = entity_key(description)
            entity = creator(description)

        entity.update()

        return entity
    except BaseException as e:
        _LOGGER.error(f"Configuring {description} failed. [{format_exception(e)}]")
        raise

class SolarmanCoordinatorEntity(CoordinatorEntity[InverterCoordinator]):
    def __init__(self, coordinator: InverterCoordinator):
        super().__init__(coordinator)
        self._attr_device_info = self.coordinator.inverter.device_info
        self._attr_state: StateType = STATE_UNKNOWN
        self._attr_native_value: StateType | str | date | datetime | time | float | Decimal = None
        self._attr_extra_state_attributes: dict[str, Any] = {}
        self._attr_value: None = None

    @property
    def device_name(self) -> str:
        return (device_entry.name_by_user or device_entry.name) if (device_entry := self.device_entry) else self.coordinator.inverter.config.name

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.coordinator.inverter.state.value > -1

    @callback
    def _handle_coordinator_update(self) -> None:
        self.update()
        self.async_write_ha_state()

    def set_state(self, state, value = None) -> bool:
        self._attr_native_value = self._attr_state = state
        if value is not None:
            self._attr_extra_state_attributes["value"] = self._attr_value = value
        return True

    def update(self):
        if (data := self.coordinator.data.get(self._attr_key)) is not None and self.set_state(*data) and self.attributes:
            if "inverse_sensor" in self.attributes and self._attr_native_value:
                self._attr_extra_state_attributes["−x"] = -self._attr_native_value
            for attr in filter(lambda a: a in self.coordinator.data, self.attributes):
                self._attr_extra_state_attributes[self.attributes[attr].replace(f"{self._attr_name} ", "")] = get_tuple(self.coordinator.data.get(attr))

class SolarmanEntity(SolarmanCoordinatorEntity):
    def __init__(self, coordinator, sensor):
        super().__init__(coordinator)

        self._attr_key = sensor["key"]
        self._attr_name = sensor["name"]
        self._attr_has_entity_name = True
        self._attr_device_class = sensor.get("class") or sensor.get("device_class")
        self._attr_translation_key = sensor.get("translation_key") or slugify(self._attr_name)
        self._attr_unique_id = slugify('_'.join(filter(None, (self.device_name, str(self.coordinator.inverter.config.serial), self._attr_key))))
        self._attr_entity_category = sensor.get("category") or sensor.get("entity_category")
        self._attr_entity_registry_enabled_default = not "disabled" in sensor
        self._attr_entity_registry_visible_default = not "hidden" in sensor
        self._attr_friendly_name = sensor.get(CONF_FRIENDLY_NAME)
        self._attr_icon = sensor.get("icon")

        if (unit_of_measurement := sensor.get("uom") or sensor.get("unit_of_measurement")):
            self._attr_native_unit_of_measurement = unit_of_measurement
        if (options := sensor.get("options")):
            self._attr_options = options
            self._attr_extra_state_attributes = self._attr_extra_state_attributes | { "options": options }
        elif "lookup" in sensor and "rule" in sensor and 0 < sensor["rule"] < 5 and (options := [s["value"] for s in sensor["lookup"]]):
            self._attr_device_class = "enum"
            self._attr_options = options
            self._attr_extra_state_attributes = self._attr_extra_state_attributes | { "options": options }
        if alt := sensor.get("alt"):
            self._attr_extra_state_attributes = self._attr_extra_state_attributes | { "Alt Name": alt }
        if description := sensor.get("description"):
            self._attr_extra_state_attributes = self._attr_extra_state_attributes | { "description": description }

        self.attributes = {slugify('_'.join(filter(None, (x, "sensor")))): x for x in attrs} if (attrs := sensor.get("attributes")) is not None else None
        self.registers = sensor.get("registers")

    def _friendly_name_internal(self) -> str | None:
        name = self.name if self.name is not UNDEFINED else None
        if self.platform and (name_translation_key := self._name_translation_key) and (n := self.platform.platform_translations.get(name_translation_key)):
            name = self._substitute_name_placeholders(n)
        elif self._attr_friendly_name:
            name = self._attr_friendly_name
        if not self.has_entity_name or not (device_name := self.device_name):
            return name
        if name is None and self.use_device_name:
            return device_name
        return f"{device_name} {name}"

class SolarmanWritableEntity(SolarmanEntity):
    def __init__(self, coordinator, sensor):
        super().__init__(coordinator, sensor)

        if not "control" in sensor:
            self._attr_entity_category = EntityCategory.CONFIG

        self.code = get_code(sensor, "write", CODE.WRITE_MULTIPLE_REGISTERS)
        self.register = min(self.registers) if len(self.registers) > 0 else None

        #dependencies:
        #  start: 0x1040
        #  length: 2
        #  data: 
        #    - 0x1053: 1
        #
        self.dependency_register = None
        self.dependency_data = None
        self.dependency_code = None
        if "dependencies" in sensor:
            self.dependency_code = get_code(sensor["dependencies"], "read", CODE.READ_HOLDING_REGISTERS)
            self.dependency_register = sensor["dependencies"]["start"]
            self.dependency_data = [None for _ in range(sensor["dependencies"]["length"])]
            if "data" in sensor["dependencies"]:
                l = sensor["dependencies"]["data"]
                for d in l:
                    for k, v in d.items():
                        self.dependency_data[k - self.dependency_register] = v
        self.deps_to_resolve = None if self.dependency_data is None else None in self.dependency_data
        self.write_register = min(self.register, self.dependency_register) if self.dependency_register is not None else self.register

        _LOGGER.warning(f"Dependency: \nCode: {self.dependency_code}\nRegister: {self.dependency_register}\nData: {self.dependency_data}")


    async def write(self, value, state = None) -> None:
        negative = value < 0
        if isinstance(value, int):
            #Signed split
            if negative:
                value = list(ssplit_p16b(value))
            #Unsigned split
            else:
                value = list(split_p16b(value))
        if isinstance(value, list):
            while len(self.registers) > len(value):
                if negative:
                    value.insert(0, 0xFFFF)
                else:
                    value.insert(0, 0)

        write_value = value
        if self.dependency_register is not None:
            data = self.dependency_data.copy()
            #Query old values
            if self.deps_to_resolve:
                old_value = await self.coordinator.inverter.call(self.dependency_code, self.dependency_register, len(self.dependency_data))
                #Replace None values with old values
                for i in range(len(data)):
                    if data[i] is None:
                        data[i] = old_value[i]
            #Copy value to data array
            offset = self.register - self.dependency_register
            write_value = ensure_list(write_value)
            for i in range(len(write_value)):
                data[offset + i] = write_value[i]
            _LOGGER.warning(f"Writing: {data}\nLength: {len(data)}\nAt: {self.dependency_register:04X}")
            write_value = data  
        
        if await self.coordinator.inverter.call(self.code, self.write_register, write_value) > 0 and state is not None:
            self.set_state(state, value)
            self.async_write_ha_state()
