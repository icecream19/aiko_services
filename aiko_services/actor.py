# Description
# ~~~~~~~~~~~
# Distributed Service based on the Actor Nodel
# - https://en.wikipedia.org/wiki/Actor_model
#
# Example 1
# ~~~~~~~~~
# from abc import abstractmethod
# from aiko_services import *
#
# class ActorTest(Actor):
#     Interface.default("ActorTest", "__main__.ActorTestImpl")
#
#     @abstractmethod
#     def test(self):
#         pass
#
# class ActorTestImpl(ActorTest):
#     def __init__(self, context):
#         context.get_implementation("Actor").__init__(self, context)
#
#     def test(self):
#         print("ActorTestImpl.test() invoked")
#
# protocol = f"{ServiceProtocol.AIKO}/actor_test:0"
# init_args = actor_args("actor_test", protocol=protocol)
# actor_test = compose_instance(ActorTestImpl, init_args)
# actor_test.test()
# aiko.process.run()
#
# Example 2
# ~~~~~~~~~
# from aiko_services import *
# actor_test = actor.ActorTest("ActorTest")
# actor_test.initialize()
# aiko.process.run()
#
# Example 3
# ~~~~~~~~~
# from aiko_services import *
# actor_test = actor.ActorTest("ActorTest")
# actor_test_proxy = proxy.ProxyAllMethods(
#    "ProxyTest", actor_test, Actor.proxy_post_message)
# actor_test_proxy.initialize()
# aiko.process.run()
#
# Design
# ~~~~~~
# Is a LifeCycleClient (can be standalone)
# Has [Leases], MailBox (ordered, priority) --> Messages
#
# Message --> Actor --> Mailbox --> Event loop --> Message (optional)
# StateChange external --> Message --> Actor --> Mailbox --> Event loop
# StateChange internal --> Priority Mailbox --> Event loop
#
# To Do
# ~~~~~
# - If default ActorImpl is too heavy, then create lightweight ActorCoreImpl
#
# * Provide "priority" mailbox for internal requirements, e.g
#   - If an Exception is raised during initialization / setup,
#     then post a "(raise_exception exception)" message to the mailbox
#
# * Support multiple Actors per process, e.g Pipeline --> PipelineElements
#   * Specify framework generated unique Actor name as ...
#         Fully Qualified Name: (actortype namespace/host/pid/sid)
#         Short Name: actortype/pid/sid  # for human input and display
#     - Allow multiple Actors in the same process to share ECConsumer instance
#       - Must support Pipeline --> PipelineElements in the same process !
#         All would share ECConsumers for ServiceDiscovery and LifeCycleClient
#
# - State Machine ...
#   - Consolidate  self.share["lifecycle"] and self.share["running"]
#     - into self.share["state"] ?
#   - Turn "self.is_running()" into "self.get_state()"
#   - Stop Actor by changing "self.share" to "STOP"
#   - Hard terminate Actor using "self._terminate()"
#
# - function() > Remote Actor >  Message > Mailbox > Event loop > function()
# - Wrapper for function tracing (enter:count, exit:time+average)
# - Optionally add Leases to Messages ... what does this mean ?
#   - Lease UUID used later: complete, delete, update (extend)
#   - Message response required ?
#   - Hold onto resources, e.g entry in a list / dictionary ?
#   - LifeCycle: still alive (state change)
#
# - Carefully rename all "function" to either "function" or "method"
# - Consider whether to make Actor an interface (with an implementation)
# - Actor(): "__init__()": Add "uuid" to "name" (for non-singletons) ?
#
# - Distributed: single process, pyro5, MQTT, Ray
#   - As an AikoService
# - Eventual consistency: add, update (dictionary only), remove
# - Logging: Distributed collection and distribution --> Kafka ?
# - Multiple threads and multithreading support
# - State Machine

from abc import abstractmethod
import os
import traceback

from aiko_services import *
from aiko_services.utilities import *

__all__ = ["Actor", "ActorImpl", "ActorTest", "ActorTestImpl", "ActorTopic"]

_AIKO_LOG_LEVEL_ACTOR = os.environ.get("AIKO_LOG_LEVEL_ACTOR", "INFO")
_LOGGER = aiko.logger(__name__, log_level=_AIKO_LOG_LEVEL_ACTOR)

class Message:
    def __init__(self, target_object, command, arguments, target_function=None):
        self.target_object = target_object
        self.command = command
        self.arguments = arguments
        self.target_function = target_function

    def __repr__(self):
        return f"Message: {self.command}({str(self.arguments)[1:-1]})"

    def invoke(self):
        if _LOGGER.isEnabledFor(DEBUG):  # Don't expand debug message
            _LOGGER.debug(f"Message.invoke(): {self}")
        target_function = self.target_function
        if not target_function:
            try:
                target_function = self.target_object.__getattribute__(
                    self.command)
            except AttributeError:
                pass

        diagnostic = None
        if target_function is None:
            diagnostic = f"{self}: Function not found in: {self.target_object}"
        else:
            if callable(target_function):
                try:
                    target_function(*self.arguments)
                except TypeError as type_error:
                    diagnostic = f"{self}: {type_error}"
            else:
                diagnostic = f"{self}: isn't callable"
        if diagnostic:
        #   raise RuntimeError(diagnostic)  # TODO: Enable traceback option
            _LOGGER.error(diagnostic)  # Was ... raise RuntimeError(diagnostic)

class ActorTopic:
    # Application topics
    IN = "in"
    OUT = "out"

    # Framework topics
    CONTROL = "control"
    STATE = "state"

    topics = [CONTROL, STATE, IN, OUT]

    def __init__(self, topic_name):  # TODO: Implement user defined topics
        self.topic_name = topic_name

class Actor(Service):
    Interface.default("Actor", "aiko_services.actor.ActorImpl")

#   @abstractmethod
#   def run(self):  # TODO: Decide what methods are required to be an Actor
#       pass

class ActorImpl(Actor):
    @classmethod
    def proxy_post_message(
        cls, proxy_name, actual_object, actual_function, *args, **kwargs):

        command = actual_function.__name__
        control_command = command.startswith(f"{ActorTopic.CONTROL}_")
        topic = ActorTopic.CONTROL if control_command else ActorTopic.IN

        actual_object._post_message(
            topic, command, args, target_function=actual_function)

    def __init__(self, context):
        context.get_implementation("Service").__init__(self, context)
        if not hasattr(self, "logger"):
            self.logger = aiko.logger(context.name)

        self.share = {
            "lifecycle": "ready",
            "log_level": get_log_level_name(self.logger),
            "running": False  # TODO: Consolidate into self.share ?
        }
        self.ec_producer = ECProducer(self, self.share)
        self.ec_producer.add_handler(self.ec_producer_change_handler)

        # First mailbox added has priority handling for all posted messages
        for topic in [ActorTopic.CONTROL, ActorTopic.IN]:
            mailbox_name = self._actor_mailbox_name(topic)
            event.add_mailbox_handler(self._mailbox_handler, mailbox_name)
        self.add_message_handler(self._topic_in_handler, self.topic_in)
        # TODO: Optionally, binary=True ?

    def _actor_mailbox_name(self, topic):
        return f"{self.name}/{self.service_id}/{topic}"

    def _mailbox_handler(self, topic, message, time_posted):
        message.invoke()

    def _topic_in_handler(self, _aiko, topic, payload_in):
        command, parameters = parse(payload_in)
    #   if _LOGGER.isEnabledFor(DEBUG):  # Don't expand debug message
    #       _LOGGER.debug(
    #           f"{self.name}: topic_in_handler(): {command}:{parameters}"
    #       )
        self._post_message(ActorTopic.IN, command, parameters)

    def _post_message(self, topic, command, args, target_function=None):
        target_object = self
        message = Message(
            target_object, command, args, target_function=target_function)
        event.mailbox_put(self._actor_mailbox_name(topic), message)

    def __repr__(self):
        return f"[{self.__module__}.{type(self).__name__} " \
               f"object at {hex(id(self))}]"

    # TODO: make public
    def _stop(self):
        aiko.process.terminate()

    def ec_producer_change_handler(self, command, item_name, item_value):
    #   if _LOGGER.isEnabledFor(DEBUG):  # Don't expand debug message
    #       _LOGGER.debug(f"ECProducer: {command} {item_name} {item_value}")
        if item_name == "log_level":
            try:
                self.logger.setLevel(str(item_value).upper())
            except ValueError:
                pass

    def is_running(self):
        return self.share["running"]

    def run(self):
        self.share["running"] = True
        try:
            aiko.process.run()
        except Exception as exception:
        #   _LOGGER.error(f"Exception caught in {self.__class__.__name__}: {type(exception).__name__}: {exception}")
            _LOGGER.error(traceback.format_exc())
            raise exception
        self.share["running"] = False

    def set_log_level(self, level):  # Override to set subclass _LOGGER level
        pass

class ActorTest(Actor):  # TODO: Move into "../examples/"
    Interface.default("ActorTest", "aiko_services.actor.ActorTestImpl")

    __test__ = False  # Stop PyTest from collecting and instantiating this class

    @abstractmethod
    def initialize(self):
        pass

    @abstractmethod
    def control_test(self, value):
        pass

    @abstractmethod
    def test(self, value):
        pass

class ActorTestImpl(ActorTest):  # TODO: Move into "../examples/"
    def __init__(self, context):
        context.get_implementation("Actor").__init__(self, context)
        self.test_count = None

    def initialize(self):
        self.control_test(0)
        self.test(1)
        self.test(2)
        self.control_test(3)
        self.test_count = 4

    def _mailbox_handler(self, topic, message, time_posted):
        print(f"Actor.handle(): {time_posted%100:07.3f}, " \
              f"{topic}: {message.command}{message.arguments}")
        super()._mailbox_handler(topic, message, time_posted)

        if topic == self._actor_mailbox_name(ActorTopic.IN):
            if self.test_count and self.test_count <= 5:
                self.control_test(self.test_count)
                self.test_count += 1

    def control_test(self, value):
        print(f"ActorTest: control_test({value})")

    def test(self, value):
        print(f"ActorTest: test({value})")
