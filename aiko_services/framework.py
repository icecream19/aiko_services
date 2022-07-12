# To Do
# ~~~~~
# * Support multiple Services per process, e.g Registrar and LifeCycleState
#   * Specify framework generated unique Service name as ...
#         Fully Qualified Name: (servicetype namespace/host/pid[.id])
#         Short Name: servicetype_pid[.id]  # for human input and display
#     * Improve Topic Path to support multiple Services in the same process ...
#           namespace/host/pid[.id]
#     - Allow multiple Services in the same process to share ECConsumer instance
#       - Could have Registrar plus LifecycleState in the same process !
#         Both would monitor "namespace/+/+/state" for difference reasons
#
# * Improve Topic Path to support multiple Services in the same process  ...
#       namespace/host/pid[.id]
#
# * Use ServiceField everywhere to elimate service[?] literal integers !
#
# - Turn into a Service Class with methods
#   - How to provide "aiko" reference to an instance of the Class ?
#   - Rename "framework.py" to "service.py"
# - Add __all__ for all public functions
#
# - For standalone applications, i.e not using messages or services,
#     don't start-up MQTT instance, because it takes time to connect
#
# - Rename "framework.py" to "service.py" and create a Service class ?
# - Implement Aiko class with "class public variables" becoming Aiko class
#   instance variables
#   - "class private" becomes one of the Aiko class instance variables
#
# - Store Aiko class instance as the "ContextManager.__init__(aiko=)" parameter
#
# - Implement message to change logging level !
#
# To Do
# ~~~~~
# - #0: Bootstrap
#
# - #1: Create Service Abstract Base Class
#   - Provide access to "aiko.public" variables
#
# - #2: Integrate aiko_services_internal/video/video_process_service.py !
#   - IoC Pipeline stream processing
#   - Refactor Service discovery across Hosts / Containers into own file
#   - Handle Stream Frame handler resulting in multiple detections !
#     - Another ML Design Pattern !
#
# - on_message() ...
#   - try ... except: around common exceptions, e.g get unknown parameter
#   - Debug options (set via topic_control) ...
#     - Print incoming message topic and payload_in
#     - Print or publish(topic_log) any exceptions raised
#
# - Move silverbrane-worker/silverbrane/services/utilities/artifacts.py here
#   - See aiko_services/z_miscellaneous/artifacts.py
#
# - Iterator as frames are received, e.g telemetry, video
# - Correct use of /control, /state, /in, /out
# - Implement stream processing

# --------------------------------------------------------------------------- #

import logging
import os
import sys
import time
import traceback

from aiko_services import *
from aiko_services.message import *
from aiko_services.utilities import *

# import aiko_services.framework as aks                      # TODO: Replace V1

__all__ = [
    "AIKO_PROTOCOL_PREFIX", "ServiceField", "public",
    "add_message_handler", "remove_message_handler",
    "add_topic_in_handler", "set_registrar_handler",
    "add_stream_handlers", "add_stream_frame_handler",
    "add_task_start_handler", "add_task_stop_handler",
    "add_tags", "add_tags_string", "get_parameter", "logger",
    "get_tag", "match_tags", "parse_tags", "process",
    "set_last_will_and_testament", "set_protocol",
    "set_terminate_registrar_not_found", "set_transport",
    "terminate", "wait_connected", "wait_parameters"
]

AIKO_PROTOCOL_PREFIX = "github.com/geekscape/aiko_services/protocol"
REGISTRAR_PROTOCOL = f"{AIKO_PROTOCOL_PREFIX}/registrar:0"
REGISTRAR_TOPIC = f"{get_namespace()}/service/registrar"
SERVICE_STATE_TOPIC = f"{get_namespace()}/+/+/state"

class ServiceField:  # TODO: Support integer index plus string name
    TOPIC = "TOPIC"          # 0
    PROTOCOL = "PROTOCOL"    # 1
    TRANSPORT = "TRANSPORT"  # 2
    OWNER = "OWNER"          # 3
    TAGS = "TAGS"            # 4

    fields = [TOPIC, PROTOCOL, TRANSPORT, OWNER, TAGS]

class private:
    exit_status = 0
    message_handlers = {}
    message_handlers_wildcard_topics = []
    registrar_handler = None
    stream_frame_handler = None
    task_start_handler = None
    task_stop_handler = None

class public:
#   aks_info = None                                          # TODO: Replace V1
    connection = Connection()
    message = None
    parameters = None
    protocol = None
    service_name = None
    tags = []
    terminate_registrar_not_found = False
    topic_path = get_namespace() + "/" + get_hostname() + "/" + get_pid()
    topic_path_registrar = None
    topic_control = topic_path + "/control"
    topic_in = topic_path + "/in"
    topic_log = topic_path + "/log"
    topic_out = topic_path + "/out"
    topic_state = topic_path + "/state"
    transport = "mqtt"

# TODO: LOG_MQTT == "both" means log to both MQTT and the console
# TODO: Allow LOG_MQTT value to be changed on-the-fly (via ECProducer update)

def logger(
    name,
    logging_handler_class=LoggingHandlerMQTT,
    topic=public.topic_log):

    log_mqtt = os.environ.get("LOG_MQTT", "true") == "true"
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    if log_mqtt:
        logging_handler = logging_handler_class(public, topic)
        aiko_logger = get_logger(name, log_level, logging_handler)
    else:
        aiko_logger = get_logger(name, log_level)
    return aiko_logger

_LOGGER = logger(__name__)
_LOGGER_MESSAGE = logger("MESSAGE")

def add_message_handler(message_handler, topic):
    if not topic in private.message_handlers:
        private.message_handlers[topic] = []
        if ("#" in topic) or ("+" in topic):
            private.message_handlers_wildcard_topics.append(topic)
    private.message_handlers[topic].append(message_handler)
    if public.message:
        public.message.subscribe(topic)

def remove_message_handler(message_handler, topic):
    if topic in private.message_handlers:
        if message_handler in private.message_handlers[topic]:
            private.message_handlers[topic].remove(message_handler)
        if len(private.message_handlers[topic]) == 0:
            del private.message_handlers[topic]
            if public.message:
                public.message.unsubscribe(topic)
        if topic in private.message_handlers_wildcard_topics:
            del private.message_handlers_wildcard_topics[topic]

def add_topic_in_handler(topic_handler):
    add_message_handler(topic_handler, public.topic_in)

# TODO: Consider moving all registrar related code into the Registar

def on_registrar_message(aiko_, topic, payload_in):
    command, parameters = parse(payload_in)
    action = None
    registrar = {}
    parse_okay = False
    if len(parameters) > 0:
        action = parameters[0]
        if command == "primary":
            if len(parameters) == 3 and action == "started":
                registrar["topic_path"] = parameters[1]
                registrar["timestamp"] = parameters[2]
                parse_okay = True
            if len(parameters) == 1 and action == "stopped":
                parse_okay = True

    if parse_okay:
        if action == "started":
            if public.protocol:
                # TODO: Use aiko_services.utilities.parser.generate() ?
                tags = ' '.join([str(tag) for tag in public.tags])
                topic = registrar["topic_path"] + "/in"
                owner = get_username()
                payload_out = f"(add {public.topic_path} {public.protocol} {public.transport} {owner} ({tags}))"
                public.message.publish(topic, payload=payload_out)
            public.topic_path_registrar = registrar["topic_path"]
            public.connection.update_state(ConnectionState.REGISTRAR)

        if action == "stopped":
            public.topic_path_registrar = None
            public.connection.update_state(ConnectionState.TRANSPORT)
            if public.terminate_registrar_not_found == True:
                terminate(1)

        if private.registrar_handler:
            handler_done = private.registrar_handler(public, action, registrar)

def set_registrar_handler(registrar_handler):
    private.registrar_handler = registrar_handler

def add_stream_handlers(
    task_start_handler, stream_frame_handler, task_stop_handler):
    add_task_start_handler(task_start_handler)
    add_stream_frame_handler(stream_frame_handler)
    add_task_stop_handler(task_stop_handler)
    add_message_handler(on_message_stream, SOME_TOPIC)

def add_stream_frame_handler(stream_frame_handler):
    private.stream_frame_handler = stream_frame_handler

def add_task_start_handler(task_start_handler):
    private.task_start_handler = task_start_handler

def add_task_stop_handler(task_stop_handler):
    private.task_stop_handler = task_stop_handler

def on_message_stream(topic, payload_in):
    print("Aiko V2: on_message_stream():")
    return False

def on_message(mqtt_client, userdata, message):
    try:
        event.queue_put(message, "message")
    except Exception as exception:
        print(traceback.format_exc())

def topic_search(topic, topics):
    if topic in topics:
        topics_matched = [topic]
    else:
        topics_matched = []

    for wildcard_topic in private.message_handlers_wildcard_topics:
        tokens = topic.split("/")
        wildcard_tokens = wildcard_topic.split("/")

        if wildcard_tokens[-1] == "#":
            if tokens[:-1] == wildcard_tokens[:-1]:
                topics_matched.append(wildcard_topic)
        elif tokens[0] == wildcard_tokens[0] and  \
             tokens[-1] == wildcard_tokens[-1]:
                topics_matched.append(wildcard_topic)
    return topics_matched

def message_queue_handler(message, _):
    payload_in = message.payload.decode("utf-8")
#   if _LOGGER_MESSAGE.isEnabledFor(logging.DEBUG):
#       _LOGGER_MESSAGE.debug(f"topic: {message.topic}, payload: {payload_in}")

    message_handler_list = []
    topics_matched = topic_search(message.topic, private.message_handlers)
    for topic_match in topics_matched:
        message_handler_list.extend(private.message_handlers[topic_match])

    if len(message_handler_list) > 0:
        for message_handler in message_handler_list:
            try:
                if message_handler(public, message.topic, payload_in):
                    return
            except Exception as exception:
                payload_out = traceback.format_exc()
                print(payload_out)
#               public.message.publish(public.topic_log, payload=payload_out)

def process_pipeline_arguments(pipeline=None):
    if pipeline:
        public.service_name = pipeline[0]
        public.protocol = pipeline[1]
        tags_string = pipeline[2]
        topic_path = pipeline[3]

        public.tags = [ "name=" + public.service_name ]
        parse_tags(tags_string)

        public.topic_in = topic_path + "/in"
        public.topic_state = topic_path + "/state"

#       aks.initialise_pipeline_service({                    # TODO: Replace V1
#           "name":       public.service_name,
#           "protocol":   public.protocol,
#           "tags":       tags_string,
#           "topic_path": topic_path
#       })

def initialize(pipeline=None):
    process_pipeline_arguments(pipeline)

#   public.aks_info = aks.initialise(                       # TODO: Replace V1
#       on_message=on_message,
#       protocol=public.protocol,
#       tags=public.tags
#   )

# TODO: on_connect user handler ?
# TODO: on_message user handler ?  Note: Aiko V2 provides add_message_handler() instead
# TODO: Implement protocol stuff, see set_protocol()
# TODO: Implement transport stuff, see set_transport()
# TODO: Implement tags stuff, e.g set_tags() ?

    add_message_handler(on_registrar_message, REGISTRAR_TOPIC)
    event.add_queue_handler(message_queue_handler, ["message"])

    if public.protocol:
        lwt_topic = public.topic_state
        lwt_payload = "(stopped)"
    else:
        lwt_topic = None
        lwt_payload = None
    lwt_retain = False

    public.message = MQTT(
        on_message, private.message_handlers, lwt_topic, lwt_payload, lwt_retain
    )
    context = ContextManager(public, public.message)

# TODO: Wait for and connect to message broker and handle failure,
#       discovery and reconnection

#   public.parameters = public.aks_info.parameters           # TODO: Replace V1

def process(loop_when_no_handlers=False, pipeline=None):
    initialize(pipeline)
#   wait_connected()
#   wait_parameters(0)
    event.loop(loop_when_no_handlers)
    if private.exit_status:
        sys.exit(private.exit_status)

def add_tags(tags):
    for tag in tags: public.tags.append(tag)

def add_tags_string(tags_string):
    if tags_string:
        tags = tags_string.split(",")
        add_tags(tags)

def get_parameter(name):                                     # TODO: Replace V1
#   return aks.get_parameter(name)
    return None

def match_tags(service_tags, match_tags):
    return all([tag in service_tags for tag in match_tags])

def get_tag(key, tags):
    tags = parse_tags(tags)
    return tags.get(key)

def parse_tags(tags_list):
    tags = {}
    for tag in tags_list:
        key, value = tag.split("=")
        tags[key] = value
    return tags

def set_last_will_and_testament(
    lwt_topic, lwt_payload="(stopped)", lwt_retain=False):

    public.message.set_last_will_and_testament(lwt_topic, lwt_payload, lwt_retain)

def set_protocol(protocol):
    public.protocol = protocol

def set_terminate_registrar_not_found(terminate_registrar_not_found):
    public.terminate_registrar_not_found = terminate_registrar_not_found

def set_transport(transport):
    public.transport = transport

def terminate(exit_status=0):
    private.exit_status = exit_status
    event.terminate()

def wait_connected():                                        # TODO: Replace V1
#   aks.wait_connected()
    pass

def wait_parameters(level):                                  # TODO: Replace V1
#   aks.wait_parameters(level)
    pass
