#!/usr/bin/python
# -*- coding: utf-8 -*-
# -----------------------------------------
# Phantom sample App Connector python file
# -----------------------------------------
# Version 2.1.2
# -----------------------------------------

# Python 3 Compatibility imports
from __future__ import print_function, unicode_literals

from typing import Any

# Phantom App imports
import phantom.app as phantom
import phantom.rules as phanrules
from phantom.base_connector import BaseConnector
from phantom.action_result import ActionResult

# Usage of the consts file is recommended
# from devo_consts import *
import requests
import json
from bs4 import BeautifulSoup
from bs4 import UnicodeDammit
import re
import time
import urllib
import ipaddress
import datetime

# Imports for generating the 'source_data_identifier'
import hashlib
import uuid

import epochrangeparser


class RetVal(tuple):

    def __new__(cls, val1, val2=None):
        return tuple.__new__(RetVal, (val1, val2))


class SettingsParser:
    def __init__(self, settings: dict, defaults: dict):
        for key, default in defaults.items():
            value = self.parse_setting_type(settings.get(key, default), default)
            setattr(self, key, value)

        print(f"SettingsParser: {self.values}")

    def parse_setting_type(self, value, default):
        if isinstance(default, str):
            return str(value).strip() if value else default

        if isinstance(default, int):
            return int(value) if value else default

        if isinstance(default, bool):
            return bool(value) if value else default

        if isinstance(default, dict):
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except:
                    value = value.strip()
            return value if 0 < len(value) else default

        if isinstance(default, list):
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except:
                    output_list = re.split(r"\s*,\s*", value)
                    while ("" in output_list): output_list.remove("")
                    value = output_list
            return value if 0 < len(value) else default

        return value

    @property
    def values(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith('__') and not callable(k)}


class DevoConnector(BaseConnector):

    @property
    def app_id(self) -> str:
        if not self._app_id:
            self._app_id = str(self.get_app_json().get("appid", 'unknown app id'))
        return self._app_id
    _app_id: str = None

    @property
    def app_version(self) -> str:
        if not self._app_version:
            self._app_version = str(self.get_app_json().get("app_version", '0.0.0'))
        return self._app_version
    _app_version: str = None

    @property
    def asset_id(self) -> str:
        if not self._asset_id:
            self._asset_id = str(self.get_asset_id())
        return self._asset_id
    _asset_id: str = None

    @property
    def asset_name(self) -> str:
        if not self._asset_name:
            self._asset_name = phantom.requests.get(
                    phanrules.build_phantom_rest_url("asset", self.asset_id),
                    verify=self.config.verify_server_cert
                ).json.get("name", 'unnamed_asset')
        return self._asset_name
    _asset_name: str = None

    @property
    def label(self) -> str:
        if not self._label:
            self._label = phantom.requests.get(
                    phanrules.build_phantom_rest_url("asset", self.asset_id),
                    verify=self.config.verify_server_cert
                ).json.get("configuration", {}).get("ingest", {}).get("container_label", 'events')
        return self._label
    _label: str = None

    @property
    def tags(self) -> list:
        if not self._tags:
            self._tags = phantom.requests.get(
                    phanrules.build_phantom_rest_url("asset", self.asset_id),
                    verify=self.config.verify_server_cert
                ).json.get("tags", [])
        return self._tags
    _tags: list = None

    @property
    def action_id(self) -> str:
        if not self._action_id:
            self._action_id = str(self.get_action_identifier())
        return self._action_id
    _action_id: str = None

    @property
    def cef_list(self) -> list:
        if not self._cef_list:
            response = phantom.requests.get(
                    phanrules.build_phantom_rest_url("cef") + "?page_size=0",
                    verify=self.config.verify_server_cert
                ).json.get("data", [])
            self._cef_list = [cef['name'] for cef in response]
        return self._cef_list
    _cef_list: list = None

    def __init__(self):

        # Call the BaseConnectors init first
        super(DevoConnector, self).__init__()

        self.state = None
        self.config = {}
        self.params = {}
        self.response = None
        self.response_json = None

    # ----------------------------------#
    #   CONNECTOR REST CALL FUNCTIONS   #
    # ----------------------------------#

    def _make_rest_call(self, endpoint, action_result, method="post", **kwargs):
        # **kwargs can be any additional parameters that requests.request accepts

        config = self.get_config()

        resp_json = None

        # Check of request method exists, for whatever strange reason???
        try:
            request_func = getattr(requests, method)
        except AttributeError:
            return RetVal(
                action_result.set_status(phantom.APP_ERROR, f"Invalid method: {method}"),
                resp_json
            )

        try:
            r = request_func(
                self.config.base_url + endpoint
                # , auth=(username, password),  # basic authentication
                , verify=self.config.verify_server_cert
                , **kwargs
            )
        except Exception as e:
            details = str(e)
            return RetVal(
                action_result.set_status(
                    phantom.APP_ERROR, f"Error Connecting to server. Details: {details}"
                ), resp_json
            )

        return self._process_response(r, action_result)

    # ----------------------#
    #   RESPONSE PARSERS    #
    # ----------------------#

    def _process_response(self, r, action_result):
        # store the r_text in debug data, it will get dumped in the logs if the action fails
        if hasattr(action_result, 'add_debug_data'):
            action_result.add_debug_data({'r_status_code': r.status_code})
            action_result.add_debug_data({'r_text': r.text})
            action_result.add_debug_data({'r_headers': r.headers})

        if r.status_code == 200 and "pong" in str(r.text).strip():
            return RetVal(phantom.APP_SUCCESS, r.text)

        # Process each 'Content-Type' of response separately

        # Process a json response
        if 'json' in r.headers.get('Content-Type', ''):
            return self._process_json_response(r, action_result)

        # Process an HTML response, Do this no matter what the api talks.
        # There is a high chance of a PROXY in between phantom and the rest of
        # world, in case of errors, PROXY's return HTML, this function parses
        # the error and adds it to the action_result.
        if 'html' in r.headers.get('Content-Type', ''):
            return self._process_html_response(r, action_result)

        # it's not content-type that is to be parsed, handle an empty response
        if not r.text:
            return self._process_empty_response(r, action_result)

        # everything else is actually an error at this point
        message = f"Can't process response from server. Status Code: {r.status_code} Data from server: {r.text}"

        return RetVal(action_result.set_status(phantom.APP_ERROR, message), None)

    def _process_empty_response(self, response, action_result):
        if response.status_code == 200:
            return RetVal(phantom.APP_SUCCESS, {})

        return RetVal(
            action_result.set_status(
                phantom.APP_ERROR, "Empty response and no information in the header"
            ), None
        )

    def _process_html_response(self, response, action_result):
        # An html response, treat it like an error
        status_code = response.status_code

        try:
            soup = BeautifulSoup(response.text, "html.parser")
            error_text = soup.text
            split_lines = error_text.split('\n')
            split_lines = [x.strip() for x in split_lines if x.strip()]
            error_text = '\n'.join(split_lines)
        except:
            error_text = "Cannot parse error details"

        message = "Status Code: {0}. Data from server:\n{1}\n".format(status_code, error_text)

        message = message.replace(u'{', '{{').replace(u'}', '}}')
        return RetVal(action_result.set_status(phantom.APP_ERROR, message), None)

    def _process_json_response(self, r, action_result):
        # Try a json parse
        try:
            resp_json = r.json()
        except Exception as e:
            return RetVal(
                action_result.set_status(
                    phantom.APP_ERROR, "Unable to parse JSON response. Error: {0}".format(str(e))
                ), None
            )

        if 200 <= r.status_code < 399:
            # Regular responses
            return RetVal(phantom.APP_SUCCESS, resp_json)
        else:
            # You should process the error returned in the json
            resp_json = json.loads(r.text)
            err_message = resp_json["object"][0]
            err_details = ", ".join(resp_json["object"][1::])
            error_message = f"{err_message}: {err_details}"
            # self.query_log_add(r.status_code, err_message, err_details)
            # we don't want the app to crash, so gracefully pass empty results instead
            return RetVal(phantom.APP_SUCCESS, {"object": []})

    def _devo_response_cleanup(self, data):
        # Devo responses are kinda dirty, so let's do some cleanup
        if isinstance(data, dict):
            for key, val in data.items():
                # Recursive is the only way to travel!
                if val and "null" != val:
                    # Enhance!
                    val = self._response_cleanup_helper(key, val)
                    data[key] = self._devo_response_cleanup(val)
        elif isinstance(data, str) and data != "null":
            try:
                # Sure, it SAYS it's a string, but is it actually a JSON in disguise?
                data = json.loads(data)
                # THE AUDACITY!
                return self._devo_response_cleanup(data)
            except:
                # Oopsies, it's not a valid json... Enhance!
                # Whitespace is bad
                data = data.strip()
                # Try to get all the remaining encoding under control
                data = urllib.parse.unquote_plus(data)
                # Replace encoded html entities with decoded elements
                data = UnicodeDammit(data).unicode_markup
                # All the silly escaped quotes that are silly
                data = data.replace("\\\"", "\"")
                # Fix IP addresses that are, for whatever reason, prepended with a forward slash
                if re.fullmatch(r"^/\d+\.\d+\.\d+\.\d+$", data):
                    data = re.sub(r"[^\d\.]", "", data)
                # Remove double backslash in filepaths
                data = data.replace("\\\\", "\\")

        # You've completed your parsing, put that data back where it came from or so help me
        return data

    def _response_cleanup_helper(self, column_name, column_value):
        # This is for specialty strings stored as digits, so if it isn't a digit, it's not worth computations
        if not str(column_value).isdigit():
            return column_value
        # Convert "probable" IPs from integers to IP addresses
        if self._might_be_an_ip(column_name):
            try:
                if type(ipaddress.ip_address(column_value)) is ipaddress.IPv4Address:
                    return str(ipaddress.IPv4Address(column_value))
                if type(ipaddress.ip_address(column_value)) is ipaddress.IPv6Address:
                    return str(ipaddress.IPv6Address(column_value))
            except:
                e = """It's not an IP but we need to handle the exception"""

        # Convert likely candidates from integers to datetimes
        elif self._might_be_a_date(column_name):
            try:
                return f"{datetime.datetime.utcfromtimestamp(column_value / 1e3).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z"
            except:
                e = """It's not a date but we need to handle the exception"""

        # Plain ol' data
        return column_value

    def _might_be_an_ip(self, column_name) -> bool:
        try:
            if not re.findall(r"((host|ip|s(ou)?rce?|de?st(ination)?).?(ip.?)?address|ip)", str(column_name).strip(),
                              re.IGNORECASE):
                return False
        except:
            return False
        return True

    def _might_be_a_date(self, column_name) -> bool:
        try:
            if not re.findall(r"date", str(column_name), re.IGNORECASE):
                return False
        except:
            return False
        return True

    # ---------------------#
    #   ACTION FUNCTIONS   #
    # ---------------------#

    def handle_action(self, params: dict = {}) -> bool:
        self.save_progress(f"Starting action: {self.action_id}\n{json.dumps(params, indent=4)}")

        if not getattr(self, f"_handle_{self.action_id}")(params):
            self.save_progress(f"{self.action_id} has no _handler function")
            return phantom.APP_ERROR

        return self.get_status()

    def _handle_test_connectivity(self, params: dict={}) -> bool:
        # set action parameters
        self.params = SettingsParser(settings=params, defaults={})
        self.save_progress(f"Using params: \n{json.dumps(self.params.values, indent=4)}")

        action_result = self.add_action_result(ActionResult(self.params.values))

        self.save_progress("Connecting to endpoint")

        # make rest call
        ret_val, response = self._make_rest_call(
            "/system/ping", action_result, method="get", params=None, headers=None
        )

        # Process a Devo "ping"
        if phantom.is_fail(ret_val):
            self.save_progress("Test Connectivity Failed.")
            return action_result.get_status()

        # Return success
        self.save_progress("Test Connectivity Passed")
        return action_result.set_status(phantom.APP_SUCCESS)

    def _handle_run_query(self, params: dict={}) -> bool:
        # set action parameters
        default_params = {
            "query": "",
            "time_input": self.config.run_query_default_range,
            "result_limit": self.config.run_query_result_limit,
            "query_endpoint": self.config.run_query_endpoint
        }
        self.params = SettingsParser(settings=params, defaults=default_params)
        self.save_progress(f"Using params: \n{json.dumps(self.params.values, indent=4)}")

        action_result = self.add_action_result(ActionResult(self.params.values))

        from_time, to_time = epochrangeparser.parse_range(self.params.time_input)

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.config.api_token
        }

        data = json.dumps({
            "limit": self.params.result_limit,
            "query": self.params.query,
            "from": from_time,
            "to": to_time,
            "mode": {"type": "json"}
        })

        ret_val, response = self._make_rest_call(
            self.params.query_endpoint, action_result, method="post", params=None, headers=headers, data=data
        )

        # Parse and clean up results
        results = []
        r_json = response
        if isinstance(response, str):
            r_json = json.loads(response)
        for result in r_json["object"]:
            item = self._devo_response_cleanup(result)
            for k in item.keys():
                if item[k] is not None and 0 < len(str(item[k]).strip()) and item[k] != "null":
                    item[k] = self._devo_response_cleanup(item[k])
            results.append(item)

        # Show number of returned results
        if self.config.debug_print:
            self.debug_print(f"Returned {len(results)} results.")

        # Add the response into the data section to spit out at the end of the run
        action_result.add_data(results)

        summary = action_result.update_summary({})
        summary['result_count'] = len(results)

        return action_result.set_status(phantom.APP_SUCCESS)

    def _handle_crop_logs(self, params: dict={}) -> bool:
        for log in [self.config.log_name_audit, self.config.log_name_error, self.config.log_name_on_poll]:
            pass
        return phantom.APP_SUCCESS

    def _handle_on_poll(self, params: dict={}) -> bool:
        # Set action parameters
        self.params = SettingsParser(settings=params, defaults={})

        action_result = self.add_action_result(ActionResult(self.params.values))

        time_from, time_to = epochrangeparser.parse_range(self.config.on_poll_time_range)  # Parse time input

        # REST call headers and data globs
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.config.api_token
        }

        data = json.dumps({
            "limit": self.config.on_poll_result_limit,
            "query": self.config.on_poll_query,
            "from": time_from,
            "to": time_to,
            "mode": {
                "type": "json"
            }
        })

        # Run the query
        ret_val, response = self._make_rest_call(
            '/lt-api/v2/search/query', action_result, params=None, headers=headers, data=data, method="post"
        )

        if phantom.APP_ERROR == ret_val:
            clean_text = re.sub(
                r"\s+", " ", re.sub(r"<.*?>", "", response.text.replace("\n", ""))
            )
            msg = f"Error response while running {self.asset_id} query ({response.status_code}): {clean_text}"
            self.debug_print(msg)
            return action_result.set_status(phantom.APP_ERROR)

        # Get results
        results = json.loads(response)["object"] if isinstance(response, str) else response["object"]

        self.state["last_run_results_count"] = len(results)
        self.state["last_run_success_count"] = 0
        self.state["last_run_error_count"] = 0
        self.state["last_run_containers_created"] = 0
        self.state["last_run_artifacts_created"] = 0
        self.state["last_run_duplicate_containers"] = 0
        self.state["last_run_aggregate_count"] = 0

        for result in results:
            row = self._devo_response_cleanup(result)
            alert = {}
            container_id = None

            # Prepare alert dictionary from result row
            for column, value in row.items():

                # Skip null values
                if not value:
                    continue

                # Rename column names to asset keymap settings
                if column in self.config.on_poll_key_map.keys():
                    column = self.config.on_poll_key_map[column]

                # Add value to alert dictionary
                alert[column] = value

            # Turns out this entire row was a dramatic work of baffoonery
            if 1 > len(alert):
                # Skip to next row
                continue

            # Name the container and associated alerts
            alert_name = str(alert.get(
                self.config.on_poll_name_field,
                f"Empty or no {self.config.on_poll_name_field} name column."
            ))

            # The eventdate recieved from Devo, usually aliased as "eventDate" but lets regex the query to futureproof
            event_date = str(alert.get(
                "eventdate"
                if 0 == len(re.findall(r"eventdate as[^\w+]+([\w]+)\s*,?", self.config.on_poll_query))
                else re.findall(r"eventdate as[^\w+]+([\w]+)\s*,?", self.config.on_poll_query)[0])
            )

            # Create Universally Unique Identifier if not already present
            if "source_data_identifier" not in alert:
                alert['source_data_identifier'] = str(
                    uuid.UUID(hex=self.dict_hash({"alert_name": alert_name, "event_date": event_date})))

            # Check if this artifact already exists
            params = f"?_filter_source_data_identifier=\"{alert['source_data_identifier']}\""
            uri = phanrules.build_phantom_rest_url("artifact") + params
            response = phanrules.requests.get(uri, verify=False)

            if 200 == response.status_code and 0 < response.json.get("count", 0):
                # Lol it already exists, you foolish fool
                self.state["last_run_duplicate_containers"] += 1
                continue

            cef_values = {}
            aggregation_filter = []

            for key, value in alert.items():
                if key in self.cef_list: cef_values[key] = value
                if self.config.aggregate_enable and key in self.config.aggregate_fields:
                    aggregation_filter.append(f"_filter_data__{key}=\"{urllib.parse.quote_plus(value)}\"")

            if self.config.aggregate_enable and aggregation_filter:
                params = (
                    f"?{'&'.join(aggregation_filter)}"
                    f"&_filter_create_time__gte=\"{urllib.parse.quote_plus(self.config.aggregate_range)}\""
                    "&sort=create_time&order=desc&page_size=0"
                )
                uri = phanrules.build_phantom_rest_url("artifact") + params
                response = phanrules.requests.get(uri, verify=self.config.verify_server_cert)

                if 200 == response.status_code and 0 < len(response.json.get("data", [])):
                    for artifact in response.json.get("data"):
                        container_info = self.get_container_info(container_id=artifact['container'])
                        self.debug_print(container_info)
                        if container_info[0]:
                            container_info = container_info[1]
                            if "closed" != container_info["status"].lower():
                                container_id = artifact['container_info']
                                break

                            if self.config.aggregate_closed and "closed" == container_info["status"].lower():
                                container_id = artifact['container']
                                break

            if container_id:
                artifact = {
                    "name": alert_name
                    , "type": self.config.on_poll_artifact_type
                    , "cef": cef_values
                    , "data": alert
                    , "label": self.label
                    , "tags": self.tags
                    , "severity": "medium"
                    , "identifier": alert['source_data_identifier']
                    , "version": self.config.on_poll_query_version
                    , "container_id": container_id
                    , "run_automation": False
                }

                status, message, artifact_id = self.save_artifact(artifact)

                if status:
                    self.state["last_run_aggregate_count"] += 1
                else:
                    self.state["last_run_error_count"] += 1

                continue

            container = {
                "name": alert_name
                , "source_data_identifier": f"{alert['source_data_identifier']}"
                , "label": self.label
                , "tags": self.tags
                , "run_automation": False
                , "artifacts": [{
                    "name": alert_name
                    , "type": self.config.on_poll_artifact_type
                    , "cef": cef_values
                    , "data": alert
                    , "label": self.label
                    , "tags": self.tags
                    , "identifier": alert['source_data_identifier']
                    , "version": self.config.on_poll_query_version
                }]
            }

            status, message, container_id = self.save_container(container)

            if status:
                self.state["last_run_containers_created"] += 1
            else:
                self.state["last_run_error_count"] += 1

        # Last one out, hit the lights
        return action_result.set_status(phantom.APP_SUCCESS)

    # -----------------------#
    #   LOGGING FUNCTIONS   #
    # -----------------------#

    def audit_log_add(self, message: str, asset_name: str=None):
        if not self.config.decided_list_logging or not self.config.audit_log_logging:
            return None

        try:
            success, message, decided_list = phanrules.get_list(list_name=asset_name)
        except:
            decided_list = []
        values = [
            datetime.datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
            ,
            f"{self.asset_name} (ver. {self.config.on_poll_query_version})" if None == asset_name else "Initializing..."
            , message
        ]
        decided_list.insert(0, values)
        phanrules.set_list(list_name=self.config.log_name_audit, values=decided_list)

    def error_log_add(self, message: str, asset_name: str=None):
        if not self.config.decided_list_logging or not self.config.error_log_loging:
            return None

        try:
            success, message, ordered_list = phanrules.get_list(list_name=asset_name)
        except:
            ordered_list = []
        values = [
            datetime.datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
            , f"{self.asset_name} (ver. {self.config.on_poll_query_version})"
            , message
        ]
        ordered_list.insert(0, values)
        phanrules.set_list(list_name=self.config.log_name_audit, values=ordered_list)

    def onpoll_log_add(self, status: bool, message: str, asset_name: str=None):
        if not self.config.decided_list_logging or not self.config.on_poll_log_loging:
            return None

        try:
            success, message, ordered_list = phanrules.get_list(list_name=asset_name)
        except:
            ordered_list = []
        values = [
            datetime.datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
            , f"{self.asset_name} (ver. {self.config.on_poll_query_version})"
            , status
            , message
        ]
        ordered_list.insert(0, values)
        phanrules.set_list(list_name=self.config.log_name_audit, values=ordered_list)

    def crop_log(self, log_name: str, time_range:str="1d"):
        success, message, ordered_list = phanrules.get_list(list_name=log_name)
        if self.config.debug_print:
            self.debug_print(type(ordered_list))
        pass

    # ---------------------#
    #   HELPER FUNCTIONS   #
    # ---------------------#

    def dict_hash(self, dictionary: dict) -> str:
        {k: dictionary[k] for k in sorted(dictionary)}
        return self.str_hash(str(dictionary))

    def str_hash(self, input_str: str) -> str:
        return hashlib.md5(str(input_str).encode(), usedforsecurity=False).hexdigest()

    def field_list_from_string(self, input_str: str) -> list:
        output_list = re.split(r"\s*,\s*", input_str)
        while ("" in output_list): output_list.remove("")
        return output_list

    # --------------------#
    #   SOAR CONSTRUCTS   #
    # --------------------#

    def initialize(self):

        # Load the state in initialize, use it to store data that needs to be accessed across actions
        self.state = self.load_state()

        # Set the asset configuration
        config_default = {
            "base_url": "",
            "api_token": "",
            "verify_server_cert": False,
            "query_endpoint": "/lt-api/v2/search/query",

            # Log names
            "log_name_error": "devoQueryandIngest_ErrorLog",
            "log_name_audit": "devoQueryandIngest_AuditLog",
            "log_name_on_poll": "devoQueryandIngest_OnPollLog",

            # Logging Enable
            "debug_print": True,
            "decided_list_logging": False,
            "audit_log_logging": False,
            "error_log_loging": False,
            "on_poll_log_loging": False,

            # run_query action default parameters
            "run_query_default_range": "2w-now",
            "run_query_result_limit": 1000,
            "run_query_endpoint": "/lt-api/v2/search/query",

            # on_poll action default parameters
            "on_poll_query": "",
            "on_poll_time_range": "1d-now",
            "on_poll_result_limit": 1000,
            "on_poll_name_field": "signature",
            "on_poll_query_version": 1,
            "on_poll_artifact_type": "host",
            "on_poll_key_map": {},

            # Internal aggregation configuration
            "aggregate_enable": True,
            "aggregate_fields": "",
            "aggregate_closed": False,
            "aggregate_range": "1d"
        }
        self.config = SettingsParser(settings=self.get_config(), defaults=config_default)

        return phantom.APP_SUCCESS

    def finalize(self):
        # Save the state, this data is saved across actions and app upgrades
        self.save_state(self.state)
        return phantom.APP_SUCCESS


def main():
    import argparse

    argparser = argparse.ArgumentParser()

    argparser.add_argument('input_test_json', help='Input Test JSON file')
    argparser.add_argument('-u', '--username', help='username', required=False)
    argparser.add_argument('-p', '--password', help='password', required=False)

    args = argparser.parse_args()
    session_id = None

    username = args.username
    password = args.password

    if username is not None and password is None:
        # User specified a username but not a password, so ask
        import getpass
        password = getpass.getpass("Password: ")

    if username and password:
        try:
            login_url = DevoConnector._get_phantom_base_url() + '/login'

            print("Accessing the Login page")
            r = phanrules.requests.get(login_url, verify=False)
            csrftoken = r.cookies['csrftoken']

            data = dict()
            data['username'] = username
            data['password'] = password
            data['csrfmiddlewaretoken'] = csrftoken

            headers = dict()
            headers['Cookie'] = 'csrftoken=' + csrftoken
            headers['Referer'] = login_url

            print("Logging into Platform to get the session id")
            r2 = phanrules.requests.post(login_url, verify=False, data=data, headers=headers)
            session_id = r2.cookies['sessionid']
        except Exception as e:
            print("Unable to get session id from the platform. Error: " + str(e))
            exit(1)

    with open(args.input_test_json) as f:
        in_json = f.read()
        in_json = json.loads(in_json)
        print(json.dumps(in_json, indent=4))

        connector = DevoConnector()
        connector.print_progress_message = True

        if session_id is not None:
            in_json['user_session_token'] = session_id
            connector._set_csrf_info(csrftoken, headers['Referer'])

        ret_val = connector._handle_action(json.dumps(in_json), None)
        print(json.dumps(json.loads(ret_val), indent=4))

    exit(0)


if __name__ == '__main__':
    main()
