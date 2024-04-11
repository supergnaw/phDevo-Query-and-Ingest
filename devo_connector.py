#!/usr/bin/python
# -*- coding: utf-8 -*-
# -----------------------------------------
# Phantom sample App Connector python file
# -----------------------------------------
# Version 2.0.7
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
import datetime
import urllib
import ipaddress
import datetime

# Imports for generating the 'source_data_identifier'
import hashlib
import uuid


class RetVal(tuple):

    def __new__(cls, val1, val2=None):
        return tuple.__new__(RetVal, (val1, val2))


class DevoConnector(BaseConnector):

    def __init__(self):

        # Call the BaseConnectors init first
        super(DevoConnector, self).__init__()

        self._aggregate_fields = None
        self._state = None

        # Variable to hold a base_url in case the app makes REST calls
        # Do note that the app json defines the asset config, so please
        # modify this as you deem fit.
        self._base_url = None
        self._api_token = None
        self._ingest_table = None
        self._ingest_range = None

    # -----------------------------------#
    #   CONNECTOR REST CALL FUNCTIONS   #
    # -----------------------------------#

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
                self._base_url + endpoint
                # , auth=(username, password),  # basic authentication
                , verify=self._verify_server_cert
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
    #   RESPONSE PARSERS   #
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

    def devo_response_cleanup(self, data):
        # Devo responses are kinda dirty, so let's do some cleanup
        if isinstance(data, dict):
            for key, val in data.items():
                # Recursive is the only way to travel!
                if val is not "null" and val != None:
                    # Enhance!
                    val = self._response_cleanup_helper(key, val)
                    data[key] = self.devo_response_cleanup(val)
        elif isinstance(data, str) and data != "null":
            try:
                # Sure, it SAYS it's a string, but is it actually a JSON in disguise?
                data = json.loads(data)
                # THE AUDACITY!
                return self.devo_response_cleanup(data)
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
        if self._might_be_an_ip(column_name, column_value):
            try:
                if type(ipaddress.ip_address(column_value)) is ipaddress.IPv4Address:
                    return str(ipaddress.IPv4Address(column_value))
                if type(ipaddress.ip_address(column_value)) is ipaddress.IPv6Address:
                    return str(ipaddress.IPv6Address(column_value))
            except:
                e = """It's not an IP but we need to handle the exception"""

        # Convert likely candidates from integers to datetimes
        elif self._might_be_a_date(column_name, column_value):
            try:
                return f"{datetime.datetime.utcfromtimestamp(column_value / 1e3).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z"
            except:
                e = """It's not a date but we need to handle the exception"""

        # Plain ol' data
        return column_value

    def _might_be_an_ip(self, column_name, column_value):
        try:
            if not re.findall(r"((host|ip|s(ou)?rce?|de?st(ination)?).?(ip.?)?address|ip)", str(column_name).strip(),
                              re.IGNORECASE):
                return False
        except:
            return False
        return True

    def _might_be_a_date(self, column_name, column_value):
        try:
            if not re.findall(r"date", str(column_name), re.IGNORECASE):
                return False
        except:
            return False
        return True

    # ----------------------#
    #   ACTION FUNCTIONS   #
    # ----------------------#

    def handle_action(self, param):
        ret_val = phantom.APP_SUCCESS

        # Get the action that we are supposed to execute for this App Run
        action_id = self.get_action_identifier()

        if self._debug_print:
            self.debug_print("action_id", self.get_action_identifier())

        if action_id == 'test_connectivity':
            ret_val = self._handle_test_connectivity(param)
        elif action_id == 'run_query':
            ret_val = self._handle_run_query(param)
        elif action_id == 'on_poll':
            self.audit_log_add(f"Starting 'on_poll': {self.get_asset_name}")
            ret_val = self._handle_on_poll(param)
        return ret_val

    def _handle_test_connectivity(self, param):
        # Add an action result object to self (BaseConnector) to represent the action for this param
        action_result = self.add_action_result(ActionResult(dict(param)))

        # NOTE: test connectivity does _NOT_ take any parameters
        # i.e. the param dictionary passed to this handler will be empty.
        # Also typically it does not add any data into an action_result either.
        # The status and progress messages are more important.

        self.save_progress("Connecting to endpoint")
        # make rest call
        ret_val, response = self._make_rest_call(
            "/system/ping", action_result, method="get", params=None, headers=None
        )

        # Process a Devo "ping"
        if phantom.is_fail(ret_val):
            # the call to the 3rd party device or service failed, action result should contain all the error details
            # for now the return is commented out, but after implementation, return from here
            self.save_progress("Test Connectivity Failed.")
            return action_result.get_status()

        # Return success
        self.save_progress("Test Connectivity Passed")
        return action_result.set_status(phantom.APP_SUCCESS)

    def _handle_run_query(self, param):
        action_id = self.get_action_identifier()
        self.save_progress(f"Executing query against Devo server: {action_id}")
        action_result = self.add_action_result(ActionResult(dict(param)))

        time_input = param.get("time_input", self._run_query_default_range)
        limit = param.get("result_limit", self._run_query_result_limit)

        time_start, time_end = self.parse_time_input(time_input)

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._api_token
        }

        data = json.dumps({
            "limit": limit
            , "query": param["query"]
            , "from": time_start
            , "to": time_end
            , "mode": {
                "type": "json"
            }
        })

        ret_val, response = self._make_rest_call(
            '/lt-api/v2/search/query', action_result, method="post", params=None, headers=headers, data=data
        )

        # Parse results
        results = []
        r_json = response
        if isinstance(response, str):
            r_json = json.loads(response)
        for result in r_json["object"]:
            item = self.devo_response_cleanup(result)
            for k in item.keys():
                if item[k] is not None and 0 < len(str(item[k]).strip()) and item[k] != "null":
                    item[k] = self.devo_response_cleanup(item[k])
            results.append(item)

        # Show number of returned results
        if self._debug_print:
            self.debug_print(f"Returned {len(results)} results.")

        # Add the response into the data section to spit out at the end of the run
        action_result.add_data(results)
        # Add a dictionary that is made up of the most important values from teh data into the summary
        summary = action_result.update_summary({})
        summary['result_count'] = len(results)

        return action_result.set_status(phantom.APP_SUCCESS)

    def _handle_on_poll(self, param):
        # Logging
        self.onpoll_log_add("Starting", f"{param}")
        self.audit_log_add(f"Starting on poll: {param}")
        action_result = self.add_action_result(ActionResult(dict(param)))

        # Get action parameters
        version = self._on_poll_query_version
        time_input = self._on_poll_time_range
        query = self._on_poll_query
        limit = self._on_poll_result_limit
        name_col = self._on_poll_name_field
        key_map = self._on_poll_key_map
        artifact_type = self._on_poll_artifact_type
        cef_list = self.get_cef_list()

        # Get aggregation data
        aggregate_enable = self._aggregate_enable
        aggregate_range = self._aggregate_range
        aggregate_closed = self._aggregate_closed
        aggregate_fields = self._aggregate_fields

        # Initialize instance variables
        artifact_count = 0
        container_count = 0
        duplicate_count = 0
        aggregate_count = 0
        error_count = 0
        results_count = 0
        success_count = 0

        # Parse time input
        time_start, time_end = self.parse_time_input(time_input)

        # Create REST call headers and data globs
        h = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._api_token
        }

        d = json.dumps({
            "limit": limit
            , "query": query
            , "from": time_start
            , "to": time_end
            , "mode": {
                "type": "json"
            }
        })

        # Run the query
        ret_val, response = self._make_rest_call(
            '/lt-api/v2/search/query', action_result, params=None, headers=h, data=d, method="post"
        )
        self.audit_log_add(f"API REST call completed")

        # Veryfiy query was ran successfully
        if True != ret_val:
            # Clean up any response
            clean_text = re.sub(r"\s+", " ", re.sub(r"<.*?>", "", response.text.replace("\n", "")))
            # Make a pretty error message
            msg = f"Error response while running {self.get_asset_id()} query ({response.status_code}): {clean_text}"
            # Log the error
            if self._debug_print:
                self.debug_print(msg)
            self.error_log_add(msg)
            self.audit_log_add(f"Failure: see {self._log_name_error} for details")
            self.onpoll_log_add("Failed", f"See {self._log_name_error} for details")
            error_count += 1
        else:
            # Get results
            results = json.loads(response)["object"] if isinstance(response, str) else response["object"]
            results_count = len(results)
            # results = self.devo_abhorrently_disgraceful_response_cleanup(results)
            if self._debug_print:
                self.debug_print(f"Returned {results_count} results ({type(results)}):\n{results}.")

            # Loop through results
            for r, row in enumerate(results):
                row = self.devo_response_cleanup(row)
                # Reset alert contents for each new row
                alert = {}

                # Loop through columns
                for c, col in row.items():
                    # Convert column name to new value
                    if c in key_map:
                        c = key_map[c]

                    # Do some fun value conversions or whatever
                    if col is None or 1 > len(str(col).strip()) or "null" == str(col).lower():
                        # Skip empty values
                        continue
                    else:
                        alert[c] = col

                # Alert cleaned up, insert them datums!
                if 1 > len(alert):
                    # Turns out this entire row was a dramatic work of baffoonery
                    continue
                else:
                    # Name the container and associated alerts
                    alert_name: Any = str(alert.get(name_col, "Empty or no signature name column."))
                    # The eventdate recieved from Devo, usually internally aliased as "eventDate" but let us regex it just to futureproof it
                    event_date: Any = str(alert.get(
                        "eventdate" if 0 == len(re.findall(r"eventdate as[^\w+]+([\w]+)\s*,?", query)) else
                        re.findall(r"eventdate as[^\w+]+([\w]+)\s*,?", query)[0]))

                    # Create Universally Unique Identifier if not already present
                    if "source_data_identifier" not in alert:
                        # The UUID here is calculated from the artifact dictionary hash
                        alert['source_data_identifier'] = str(
                            uuid.UUID(hex=self.dict_hash({"alert_name": alert_name, "event_date": event_date})))
                        self.onpoll_log_add("processing",
                                            f"source_data_identifier created: {alert['source_data_identifier']}")
                    else:
                        self.onpoll_log_add("processing",
                                            f"source_data_identifier detected: {alert['source_data_identifier']}")

                    # Check if this artifact already exists
                    params = f"?_filter_source_data_identifier=\"{urllib.parse.quote_plus(alert['source_data_identifier'])}\""
                    uri = phanrules.build_phantom_rest_url("artifact") + params
                    response = phanrules.requests.get(uri, verify=False)

                    if self._debug_print:
                        self.debug_print(uri)

                    # response = phanrules.requests.get(uri, verify=self._verify_server_cert)
                    if 200 == response.status_code:
                        if 0 < json.loads(response.text).get("count", 0):
                            # Lol it already exists, you foolish fool
                            duplicate_count += 1
                            if self._debug_print:
                                self.debug_print(
                                    f"duplicate artifact: {json.loads(response.text).get('data')[0].get('container')}")
                            # self.onpoll_log_add("processing", f"duplicate artifact: {json.loads(response.text).get('data')[0].get('container')}")
                            continue
                        else:
                            # Not a duplicate, keep swimming!
                            pass
                    else:
                        # Some kind of error
                        msg = f"Failed to get artifact search results from {uri}: {json.loads(response.text)}"
                        if self._debug_print:
                            self.debug_print(msg)
                        self.error_log_add(msg)
                        continue

                    # Extract CEF and aggregation data for container
                    cef_values = {}
                    aggregation_filter = []
                    if aggregate_enable:
                        if 0 == len(aggregate_fields):
                            aggregate_fields = cef_list
                    for key, value in alert.items():
                        if key in cef_list:
                            cef_values[key] = value
                        if aggregate_enable and key in aggregate_fields:
                            aggregation_filter.append(f"_filter_data__{key}=\"{urllib.parse.quote_plus(value)}\"")

                    # Check for aggregations
                    container_id = None
                    if aggregate_enable:
                        # Create API URI Parameters
                        params = f"?{'&'.join(aggregation_filter)}&_filter_create_time__gte=\"{urllib.parse.quote_plus(aggregate_range)}\"&sort=create_time&order=desc&page_size=0"
                        # Fails to parse:
                        uri = phanrules.build_phantom_rest_url("artifact") + params
                        self.onpoll_log_add("processing", f"aggregation search uri: {uri}")
                        response = phanrules.requests.get(uri, verify=self._verify_server_cert)
                        if 200 == response.status_code:
                            r = json.loads(response.text).get("data", [])
                            for artifact in r:
                                container = self.get_container_info(container_id=artifact['container'])
                                if self._debug_print:
                                    self.debug_print(f"container_info: {container}")
                                if container[0]:
                                    container = container[1]
                                    if not aggregate_closed:
                                        # Keep out the riff-raff
                                        if "closed" == container["status"].lower():
                                            continue
                                    # This is a valid target container ID, let's use it
                                    container_id = artifact['container']
                                    self.onpoll_log_add("processing",
                                                        f"Valid aggregation target container identified: {container_id}")
                                    # Break the loop
                                    break
                                else:
                                    msg = f"Failed to get container data of id {artifact['container']}"
                                    if self._debug_print:
                                        self.debug_print(msg)
                                    self.error_log_add(msg)
                        else:
                            msg = f"Failed to get artifact search results from {uri}: {json.loads(response.text)}"
                            if self._debug_print:
                                self.debug_print(msg)
                            self.error_log_add(msg)
                            continue

                    # Do the container things
                    if None != container_id:
                        artifact = {
                            "name": alert_name
                            , "type": artifact_type
                            , "cef": cef_values
                            , "data": alert
                            , "label": self.get_asset_label()
                            , "tags": self.get_asset_tags()
                            , "severity": "medium"
                            , "hash": re.sub(r"[^ABCDEFabcdef0123456789]", "", alert['source_data_identifier'])
                            , "identifier": alert['source_data_identifier']
                            , "version": version
                            , "container_id": container_id
                            , "run_automation": False
                        }
                        success, message, artifact_id = self.save_artifact(artifact)
                        if isinstance(artifact_id, int):
                            # Success!
                            success_count += 1
                            aggregate_count += 1
                            artifact_count += 1
                            if self._debug_print:
                                self.debug_print(
                                    f"Artifact ({alert['source_data_identifier']}) aggregated to container: {container_id}")
                            self.onpoll_log_add("Success",
                                                f"Artifact ({alert['source_data_identifier']}) aggregated to container: {container_id}")
                        else:
                            self.error_log_add(message)
                            error_count += 1
                    else:
                        # So this creates a new container, however...:
                        #   "Don't set run_automation to true--the API automatically sets the final artifact added as
                        #   true so playbooks only run once against each container"
                        # This is what the documentation says SHOULD happen, but it doesn't seem to work as expected
                        container = {
                            "name": alert_name
                            , "source_data_identifier": f"{alert['source_data_identifier']}"
                            , "label": self.get_asset_label()
                            , "tags": self.get_asset_tags()
                            , "run_automation": False
                            , "artifacts": [{
                                "name": alert_name
                                , "type": artifact_type
                                , "cef": cef_values
                                , "data": alert
                                , "label": self.get_asset_label()
                                , "tags": self.get_asset_tags()
                                , "hash": re.sub(r"[^ABCDEFabcdef0123456789]", "", alert['source_data_identifier'])
                                , "identifier": alert['source_data_identifier']
                                , "version": version
                            }]
                        }

                        # https://docs.splunk.com/Documentation/Phantom/4.10.7/DevelopApps/AppDevAPIRef
                        # Returns tuple: (status [phantom.APP_SUCCESS|phantom.APP_ERROR], message, id [container ID or None on fail])
                        status, msg, container_id = self.save_container(container)
                        if status and isinstance(container_id, int):
                            # Success!
                            success_count += 1
                            container_count += 1
                            artifact_count += 1
                            if self._debug_print:
                                self.debug_print(f"Container created: {container_id}")
                                self.debug_print(f"Artifact added: {alert['source_data_identifier']}")
                                self.debug_print(f"Message: {msg}")
                        else:
                            # We dun goof'd
                            error_count += 1
                            # Oh, how the turntables
                            self.error_log_add(msg)
                            self.onpoll_log_add("Failed",
                                                f"Error while attempting to create new container with new artifact ({alert['source_data_identifier']}): {msg}")

        # Save overall progress for statistical analysisenthesis
        self._state["last_run_results_count"] = results_count
        self._state["last_run_success_count"] = success_count
        self._state["last_run_error_count"] = error_count
        self._state["last_run_containers_created"] = container_count
        self._state["last_run_artifacts_created"] = artifact_count
        self._state["last_run_duplicate_containers"] = duplicate_count
        self._state["last_run_aggregate_count"] = aggregate_count

        action_result.update_summary({
            "results_count": results_count
            , "success_count": success_count
            , "error_count": error_count
            , "container_count": container_count
            , "artifact_count": artifact_count
            , "duplicate_count": duplicate_count
            , "aggregate_count": aggregate_count
        })

        self.save_progress("Saving last run progress.")
        # Set final action status
        if 0 < error_count:
            final_status = phantom.APP_ERROR
            final_msg_status = "Failed"
        else:
            final_status = phantom.APP_SUCCESS
            final_msg_status = "Succeeded"

        # Log the results
        self.onpoll_log_add(final_msg_status, f"Results: {results_count}")
        if 0 < results_count:
            if 0 < success_count:
                self.onpoll_log_add(final_msg_status, f"Successes: {success_count}")
            if 0 < error_count:
                self.onpoll_log_add(final_msg_status, f"Errors: {error_count}")
            if 0 < duplicate_count:
                self.onpoll_log_add(final_msg_status, f"Duplicates: {duplicate_count}")
            if 0 < container_count:
                self.onpoll_log_add(final_msg_status, f"Containers: {container_count}")
            if 0 < artifact_count:
                self.onpoll_log_add(final_msg_status, f"Artifacts: {artifact_count}")
            if 0 < aggregate_count:
                self.onpoll_log_add(final_msg_status, f"Aggregations: {aggregate_count}")

        # Last one out, hit the lights
        return action_result.set_status(final_status)

    def _handle_crop_logs(self, param):
        for log in [self._log_name_audit, self._log_name_error, self._log_name_on_poll]:
            pass
        return phantom.APP_SUCCESS

    # -----------------------#
    #   LOGGING FUNCTIONS   #
    # -----------------------#

    def audit_log_add(self, message, asset_name=None):
        if not self._decided_list_logging or not self._audit_log_logging:
            return None

        try:
            success, message, decided_list = phanrules.get_list(list_name=asset_name)
        except:
            decided_list = []
        values = [
            datetime.datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
            ,
            f"{self.get_asset_name()} (ver. {self._on_poll_query_version})" if None == asset_name else "Initializing..."
            , message
        ]
        decided_list.insert(0, values)
        phanrules.set_list(list_name=self._log_name_audit, values=decided_list)

    def error_log_add(self, message):
        if not self._decided_list_logging or not self._error_log_loging:
            return None

        try:
            success, message, ordered_list = phanrules.get_list(list_name=asset_name)
        except:
            ordered_list = []
        values = [
            datetime.datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
            , f"{self.get_asset_name()} (ver. {self._on_poll_query_version})"
            , message
        ]
        ordered_list.insert(0, values)
        phanrules.set_list(list_name=self._log_name_audit, values=ordered_list)

    def onpoll_log_add(self, status, message):
        if not self._decided_list_logging or not self._onpoll_log_loging:
            return None

        try:
            success, message, ordered_list = phanrules.get_list(list_name=asset_name)
        except:
            ordered_list = []
        values = [
            datetime.datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
            , f"{self.get_asset_name()} (ver. {self._on_poll_query_version})"
            , status
            , message
        ]
        ordered_list.insert(0, values)
        phanrules.set_list(list_name=self._log_name_audit, values=ordered_list)

    def crop_log(self, log_name, time_range="1d"):
        success, message, ordered_list = phanrules.get_list(list_name=log_name)
        if self._debug_print:
            self.debug_print(type(ordered_list))
        pass

    # -------------------------#
    #   SOAR REST API CALLS   #
    # -------------------------#

    def get_cef_list(self):
        # Make REST call to SOAR
        uri = phanrules.build_phantom_rest_url("cef") + "?page_size=0"
        response = phanrules.requests.get(uri, verify=self._verify_server_cert)
        if 200 > response.status_code or 299 < response.status_code:
            self.error_log_add(f"Failed SOAR API call: {json.loads(response.text)['message']}")
            return []

        # Aggregate all CEFs into a single list
        all_cefs = []
        for cef in json.loads(response.text)['data']:
            all_cefs.append(cef["name"])

        return all_cefs

    def get_asset_name(self):
        # Make REST call to SOAR
        uri = phanrules.build_phantom_rest_url("asset", self.get_asset_id())
        return json.loads(phanrules.requests.get(uri, verify=self._verify_server_cert).text).get("name",
                                                                                                 "unnamed_asset")

    def get_asset_tags(self):
        # Make REST call to SOAR
        uri = phanrules.build_phantom_rest_url("asset", self.get_asset_id())
        return json.loads(phanrules.requests.get(uri, verify=self._verify_server_cert).text).get("tags", [])

    def get_asset_label(self):
        # Make REST call to SOAR
        uri = phanrules.build_phantom_rest_url("asset", self.get_asset_id())
        return json.loads(phanrules.requests.get(uri, verify=self._verify_server_cert).text).get("configuration",
                                                                                                 {}).get("ingest",
                                                                                                         {}).get(
            "container_label", None)

    # ------------------#
    #   TIME PARSERS   #
    # ------------------#

    def parse_time_input(self, time_input):
        # Parse input string to find timestamps and time increments
        pattern = r'((\d{4}\-\d{2}\-\d{2}(\s\d{2}:\d{2}(:\d{2})?)?)|\d+[smhdwby]|now)'
        matches = re.findall(pattern, time_input)

        # Validate correct input and correct mistakes or invalid inputs
        if 1 > len(matches):
            # Completely wrong input
            matches = [("now",), (self._run_query_default_range,)]
        if 2 > len(matches):
            # Only one match
            if "now" not in matches[0]:
                # Only a timestamp or date range exists, so add "now" as anchor
                matches.append(("now",))
            else:
                # Only "now" exists as anchor, so add range for calculations
                matches.append((self._run_query_default_range,))
        if 3 > len(matches):
            # Mostly correct, just verify not two "now" strings present
            if "now" == matches[0][0] and "now" == matches[1][0]:
                # Okay, we almost made it, but there's two "now" strings, so use default ingest range instead
                matches.pop(1)
                matches.append((self._run_query_default_range,))

        # Pretty-up input variable names
        time_input_a = matches[0][0]
        time_input_b = matches[1][0]

        # Check input types and parse accordingly
        if self.is_timestamp(time_input_a) and self.is_timestamp(time_input_b):
            # If both Y-m-d <H:i:s>, parse to epoch seconds and get min/max for beg/end
            time_input_a = self.parse_timestamp(time_input_a)
            time_input_b = self.parse_timestamp(time_input_b)
            time_input_beg = min(time_input_a, time_input_b)
            time_input_end = max(time_input_a, time_input_b)
        elif self.is_time_increment(time_input_a) and self.is_time_increment(time_input_b):
            # If both increment (eg, 2d), parse to epoch seconds and get difference from now() anchor
            time_input_a = self.parse_time_increment(time_input_a)
            time_input_b = self.parse_time_increment(time_input_b)
            time_input_beg = time.time() - max(time_input_a, time_input_b)
            time_input_end = time.time() - min(time_input_a, time_input_b)
        elif "now" == time_input_a or "now" == time_input_b:
            # If one input is "now", parse other input to epoc seconds and get difference from now() anchor
            time_input_end = time.time()
            increment = time_input_a if time_input_a != "now" else time_input_b
            if self.is_timestamp(increment):
                # It's a timestamp!
                time_input_beg = self.parse_timestamp(increment)
            else:
                # It's a time range!
                time_input_beg = time.time() - self.parse_time_increment(increment)
        else:
            # If we get here, one input is a timestamp, the other is a time increment
            # If input a is an increment, use it as the increment, otherwise use input b
            increment = time_input_a if self.is_time_increment(time_input_a) else time_input_b
            # If input a is a timestamp, use it as the anchor, otherwise use input b
            anchor = time_input_a if self.is_timestamp(time_input_a) else time_input_b
            # Remove both inputs from the time input and get the offset (either "-" or "+")
            offset = time_input.replace(time_input_a, "").replace(time_input_b, "").strip()
            # In case the offset character isn't found, default to "-"
            offset = "-" if 1 != len(offset) else offset
            if "+" == offset:
                # The increment should be offset into the future from the anchor
                time_input_beg = self.parse_timestamp(anchor)
                time_input_end = time_input_beg + self.parse_time_increment(increment)
            else:
                # The increment should be offset into the past from the anchor
                time_input_end = self.parse_timestamp(anchor)
                time_input_beg = time_input_end - self.parse_time_increment(increment)

        # Input validation and parsing complete!
        return time_input_beg, time_input_end

    def parse_time_increment(self, t_str):
        t_str = t_str.lower().strip()

        if "now" == t_str:
            return time.time()

        if re.fullmatch(r'^\d+(s|m|h|d|w|b|y)$', t_str):
            t_diff = 0
            quantity = int(t_str[0:-1])
            interval = t_str[-1]

            if 'y' == interval:
                # year
                t_diff = t_diff + (quantity * 31536000)
            if 'b' == interval:
                # month
                t_diff = t_diff + (quantity * 2678400)
            if 'w' == interval:
                # week
                t_diff = t_diff + (quantity * 604800)
            if 'd' == interval:
                # day
                t_diff = t_diff + (quantity * 86400)
            if 'h' == interval:
                # hour
                t_diff = t_diff + (quantity * 3600)
            if 'm' == interval:
                # minute
                t_diff = t_diff + (quantity * 60)
            if 's' == interval:
                # second
                t_diff = t_diff + (quantity * 1)
            return t_diff
        else:
            # No matches? This shouldn't happen, but just in case...default is our friend!
            msg = f"Failed to properly parse time inchrement: {t_str}"
            if self._debug_print:
                self.debug_print(msg)
            self.error_log_add(msg)
            return self.parse_time_increment(self._run_query_default_range)

    def parse_timestamp(self, t_str):
        pattern = r'^\d{4}\-\d{2}\-\d{2}(\s\d{2}:\d{2}(:\d{2})?)?$'
        matches = re.fullmatch(pattern, t_str)
        if matches:
            # Python is silly and can't auto detect formats so let's just brute-force it unstead of 30 lines of if-else statements
            try:
                t_str = datetime.datetime.now().strptime(matches[0], "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                try:
                    t_str = datetime.datetime.now().strptime(matches[0], "%Y-%m-%d %H:%M").timestamp()
                except Exception:
                    try:
                        t_str = datetime.datetime.now().strptime(matches[0], "%Y-%m-%d").timestamp()
                    except Exception:
                        # It already regex matched before this so if it gets here, there's a glitch in the matrix
                        msg = f"Failed to properly parse timestamp: {t_str}"
                        if self._debug_print:
                            self.debug_print(msg)
                        self.error_log_add(msg)
                        return None
            return t_str

    def is_time_increment(self, t_str):
        return True if re.fullmatch(re.compile(r'^\d+(s|m|h|d|w|b|y)$', re.IGNORECASE), t_str) else False

    def is_timestamp(self, t_str):
        return True if re.fullmatch(r'^\d{4}\-\d{2}\-\d{2}(\s\d{2}:\d{2}(:\d{2})?)?$', t_str) else False

    def parse_aggregate_range(self, t_str):
        seconds = self.parse_time_increment(t_str) if self.is_time_increment(t_str) else self.parse_time_increment("1d")
        t_sec = datetime.datetime.utcfromtimestamp(time.time() - seconds)
        t_stamp = t_sec.strftime("%Y-%m-%d %H:%M:%S")
        return t_stamp

    # ----------------------#
    #   HELPER FUNCTIONS   #
    # ----------------------#

    def dict_hash(self, dictionary):
        # Sort by key so {'a': 1, 'b': 2} is the same as {'b': 2, 'a': 1}
        {k: dictionary[k] for k in sorted(dictionary)}
        # Get the MD5 of the dictionary and bypass FIPS since this isn't for security
        dictionary_hash = hashlib.md5(str(dictionary).encode(), usedforsecurity=False)
        # Return hash hex digest of dictionary
        return dictionary_hash.hexdigest()

    def str_hash(self, input_str):
        return hashlib.md5(str(input_str).encode(), usedforsecurity=False).hexdigest()

    def field_list_from_string(self, input_str):
        output_list = re.split(r"\s*,\s*", input_str)
        while ("" in output_list):
            output_list.remove("")
        return output_list

    # ---------------------#
    #   SOAR CONSTRUCTS   #
    # ---------------------#

    def initialize(self):
        # Load the state in initialize, use it to store data that needs to be accessed across actions
        self._state = self.load_state()

        # get the asset configuration
        config = self.get_config()
        self._base_url = config.get("base_url")
        self._api_token = config.get("api_token")
        self._verify_server_cert = config.get("verify_server_cert", False)

        # Log names
        self._log_name_error = config.get("log_name_error", "devoQueryIngest_ErrorLog")
        self._log_name_audit = config.get("log_name_audit", "devoQueryIngest_AuditLog")
        self._log_name_on_poll = config.get("log_name_on_poll", "devoQueryIngest_OnPollLog")

        # run_query action default parameters
        self._run_query_default_range = config.get("run_query_default_range", "1w-now")
        self._run_query_result_limit = config.get("run_query_result_limit", 1000)

        # on_poll action default parameters
        self._on_poll_query = config.get("on_poll_query", "")
        self._on_poll_time_range = config.get("on_poll_time_range", "1d-now")
        self._on_poll_result_limit = config.get("on_poll_result_limit", 1000)
        self._on_poll_name_field = config.get("on_poll_name_field", "signature")
        self._on_poll_query_version = config.get("on_poll_query_version", 1)
        self._on_poll_artifact_type = config.get("on_poll_artifact_type", "host")
        self._on_poll_key_map = json.loads(config.get("on_poll_key_map", "{}"))

        # Internal aggregation configuration
        self._aggregate_enable = config.get("aggregate_enable", False)
        self._aggregate_closed = config.get("aggregate_closed", False)
        self._aggregate_fields = self.field_list_from_string(config.get("aggregate_fields", "").strip())
        self._aggregate_range = self.parse_aggregate_range(config.get("aggregate_range", "1d").strip())

        # Logging Enable
        self._debug_print = True
        self._decided_list_logging = False
        self._audit_log_logging = False
        self._error_log_loging = False
        self._onpoll_log_loging = False
        self.audit_log_add("Initializing...", self.get_asset_id())

        return phantom.APP_SUCCESS

    def finalize(self):
        # Save the state, this data is saved across actions and app upgrades
        self.save_state(self._state)
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
