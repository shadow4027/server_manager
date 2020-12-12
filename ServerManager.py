# imports
import subprocess
import queue
import threading
from os import path
from time import sleep
import sys
from shlex import split


# generic sever class
class Server(object):
    """
    class to manage the server process and control input and output using queues
    """

    def __init__(self, init_command, queue_max=10, cwd=None, shutdown_instruction=None, server_name=None,
                 print_output=True):
        # verify arguments
        if isinstance(server_name, str):
            self.name = server_name
        if isinstance(init_command, (tuple, list)):
            self.command_list = tuple(init_command)
        elif isinstance(init_command, str):
            self.command_list = tuple(split(init_command))
        else:
            raise ValueError("init_command must be type str, list, or tuple")
        # verify shutdown instruction
        if shutdown_instruction:
            if isinstance(shutdown_instruction, str):
                self._shutdown_instruction = shutdown_instruction
            elif isinstance(shutdown_instruction, (tuple, list)):  # supports a list of them
                for index, instruction in enumerate(shutdown_instruction):
                    if not isinstance(instruction, str):
                        raise ValueError(f"shutdown_instruction[{index}] not str")
                self._shutdown_instruction = tuple(shutdown_instruction)
            else:
                raise ValueError("shutdown_instruction must be type str")
        else:
            self._shutdown_instruction = None

        # sets the path for the process context
        if cwd:
            self.cwd = cwd
        else:  # if no cwd passed then try to find an absolute path inside command args
            for item in self.command_list:
                if path.isabs(item):
                    self.cwd = path.split(item)[0]
                    break
            else:  # if can't find path then set it to None
                self.cwd = None

        # queue stuff
        if not isinstance(queue_max, int):
            raise ValueError("queue_max must be an int")
        if queue_max < 1 or queue_max > 20:
            raise ValueError("queue_max must be between 1 and 20")
        self._running = False

        # create queues
        self._output_queue = queue.Queue(queue_max)
        self._input_queue = queue.Queue()
        self._error_queue = queue.Queue(queue_max)

        # create threads
        self._output_worker = None
        self._input_worker = None
        self._error_worker = None

        # create process
        self._process = None
        self._ran = False

        # flags
        self._print_flag = threading.Event()
        if print_output:
            self._print_flag.set()

    def __del__(self):
        self.shutdown()

    def run(self, debug=False):
        self._ran = True
        if debug:
            self._process = subprocess.Popen(args=self.command_list, cwd=self.cwd, universal_newlines=True, bufsize=1)
        else:
            self._process = subprocess.Popen(args=self.command_list, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, cwd=self.cwd, universal_newlines=True, bufsize=1)
        self._output_worker = threading.Thread(target=Server.output_worker,
                                               args=(self._output_queue, self._process, self._print_flag, self.name,),
                                               daemon=True)
        self._input_worker = threading.Thread(target=Server.input_worker,
                                              args=(self._input_queue, self._process,),
                                              daemon=True)
        self._error_worker = threading.Thread(target=Server.error_worker,
                                              args=(self._input_queue, self._process, self.name,),
                                              daemon=True)
        self._output_worker.start()
        self._input_worker.start()
        self._error_worker.start()
        self._running = True
        if self._process.poll() is None:
            return True
        else:
            self.shutdown()

    def read(self):
        try:
            return self._output_queue.get_nowait()
        except queue.Empty:
            return ""

    def read_error(self):
        try:
            return self._error_queue.get_nowait()
        except queue.Empty:
            return ""

    def write(self, input_string: str):
        if not type(input_string) == str:
            raise ValueError("input_string must be string")
        self._input_queue.put_nowait(input_string)

    def shutdown(self, shutdown_wait=10, instruction_delay=1):
        if self.is_running() is True:
            # gives server a chance to terminate before force closure
            if self._shutdown_instruction:
                if type(self._shutdown_instruction) == str:
                    self.write(self._shutdown_instruction)
                else:
                    for instruction in self._shutdown_instruction:
                        self.write(instruction)
                        sleep(instruction_delay)
            else:
                self._process.terminate()
            try:
                self._process.wait(shutdown_wait)
            except subprocess.TimeoutExpired:
                print(f"<{self.name} | ERROR>: Process failed to terminate")

            # wait for threads to close
            if self._output_worker.is_alive():
                self._output_worker.join()
            if self._input_worker.is_alive():
                self.write("\n")
                self._input_worker.join()
            if self._error_worker.is_alive():
                self._error_worker.join()

            if self._process.poll() is None:
                raise Exception("Process failed to close")
            else:
                self._running = False
                return self._process.returncode

    def is_running(self):
        if self._ran:
            poll_result = self._process.poll()
            if poll_result is None:
                return True
            else:
                self._running = False
                return False
        else:
            return False

    def status(self, debug=False):
        if debug:
            return self.__dict__

        status_dict = {"Running": self.is_running()}
        status_dict.update({"print_output": self._print_flag.is_set()})
        if self._ran:
            if self._running:
                status_dict.update({"Process": "ACTIVE"})
            else:
                status_dict.update({"Process": self._process.poll()})
            if self._running:
                status_dict.update({"Error_thread": self._error_worker.is_alive()})
                status_dict.update({"Output_thread": self._output_worker.is_alive()})
                status_dict.update({"Input_thread": self._input_worker.is_alive()})
        return status_dict

    def print_output(self, output=None):
        if output is None:
            return self._print_flag.is_set()
        elif output is True:
            self._print_flag.set()
        elif output is False:
            self._print_flag.clear()
        else:
            raise ValueError("output must be None or Bool")

    # class methods
    @classmethod
    def output_worker(cls, q: queue.Queue, process: subprocess.Popen, print_flag: threading.Event, server_name=None, ):
        while process.poll() is None:
            output_string = process.stdout.readline()
            if output_string:
                if server_name and print_flag.is_set():
                    print(f"<{server_name}>: {output_string.strip()}")
                if q.full():
                    q.get()
                q.put(output_string)

    @classmethod
    def input_worker(cls, q: queue.Queue, process: subprocess.Popen):
        while True:
            input_string = q.get()
            if not input_string[-1] == '\n':
                input_string += '\n'
            if process.poll() is None:
                process.stdin.write(input_string)
            else:
                break

    @classmethod
    def error_worker(cls, q: queue.Queue, process: subprocess.Popen, server_name=None):
        while process.poll() is None:
            error = process.stderr.readline()
            if error:
                if error:
                    print(f"<{server_name} | ERROR>: {error.strip()}")
                if q.full():
                    q.get()
                q.put(error)


class ServerManager(object):
    """
    Server manager class to control multiple servers at once
    """

    def __init__(self, server_commands: tuple):
        self._servers = {}
        # unpack server commands
        if isinstance(server_commands, (tuple, list)):
            for index, server_command in enumerate(server_commands):
                if isinstance(server_command, dict):
                    # extract keys
                    server_name = server_command.get("name")
                    if not server_name:
                        raise ValueError(f'server_commands{index} must contain a \"name\" key pair')

                    command = server_command.get("command")
                    if not command:
                        raise ValueError(f'server_commands{index} must contain a \"command\" key pain')

                    shutdown_instruction = server_command.get("shutdown_instruction")
                    if not shutdown_instruction:
                        print(f'<{server_name} | WARNING>: Does not contain a shutdown_instruction')

                    print(f'[{index}, {server_name}] initialize server with \"{command}\"')
                    serv = Server(command, cwd=server_command.get("cwd"),
                                  shutdown_instruction=shutdown_instruction,
                                  server_name=server_name)

                    self._servers.update({server_name: serv})
                else:
                    raise ValueError(f"server_commands[{index}] must be type dict")
        else:
            raise ValueError("server_commands must be tuple")

    def start_all(self):
        return [{server_name: self._servers[server_name].run()} for server_name in self._servers.keys()]

    def shutdown_all(self):
        return [{server_name: self._servers[server_name].shutdown()} for server_name in self._servers.keys()]

    def status_all(self):
        return [{server_name: self._servers[server_name].is_running()} for server_name in self._servers.keys()]

    def send_all(self, message):
        return [{server_name: self._servers[server_name].write(message)} for server_name in self._servers.keys()]

    def output_all(self, value: bool):
        return [{server_name: self._servers[serv_name].print_output(value)} for server_name in self._servers.keys()]


    def start(self, server_name: str):
        if isinstance(serv_name, str):
            serv = self._servers.get(server_name)
            if serv:
                return serv.run()

    def shutdown(self, server_name: str):
        if isinstance(serv_name, str):
            serv = self._servers.get(server_name)
            if serv and serv.is_running() is True:
                return serv.shutdown()

    def send(self, server_name: str, message: str):
        if isinstance(serv_name, str) and type(message) == str:
            serv = self._servers.get(server_name)
            if serv and serv.is_running() is True:
                return serv.write(message)

    def read(self, server_name: str):
        if isinstance(serv_name, str):
            serv = self._servers.get(server_name)
            if serv and serv.is_running() is True:
                return serv.read()

    def status(self, server_name: str, debug=False):
        if isinstance(serv_name, str):
            serv = self._servers.get(server_name)
            if serv:
                return serv.status(debug)
        else:
            raise ValueError("server_name must be type str")

    def output(self, server_name: str, value=None):
        serv = self._servers.get(server_name)
        if serv:
            if value is None:
                return serv.print_output()
            elif value is True or value is False:
                return serv.print_output(value)
            else:
                raise ValueError("value must be None or bool")
    # def set_flag(self, server_name: str, flag: str, value: bool):
    #     if not isinstance(serv_name, str):
    #         raise ValueError("server_name must be str")
    #     if not isinstance(flag, str):
    #         raise ValueError("flag must be str")
    #     if not isinstance(value,  bool):
    #         raise ValueError("value must be bool")


    def server_names(self):
        return self._servers.keys()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        pass
    try:
        commands = (
            {
                "command": r'java -jar "C:\Users\jbloo\Downloads\server.jar" -nogui',
                "name": "test",
                "shutdown_instruction": ("say SHUTTING DOWN", "say 5", "say 4", "say 3", "say 2", "say 1", "stop")
            },
            {
                "command": [r"C:\Users\jbloo\Downloads\bedrock-server-1.16.101.01\bedrock_server.exe"],
                "name": "bs",
                "shutdown_instruction": ("say SHUTTING DOWN", "say 5", "say 4", "say 3", "say 2", "say 1", "stop")
            }
        )
        man = ServerManager(commands)
        TERM_ALL = "$all"
        TERM_HELP = "$help"
        CONSOLE_ALIAS = "<CONSOLE>:"
        # man.start_all()
        # serv = Server(" ".join(['java', '-jar', r'C:\Users\jbloo\Downloads\server.jar', "-nogui"]),
        #               shutdown_instruction=("say SHUTTING DOWN", "say 5", "say 4", "say 3", "say 2", "say 1", "stop"))
        # serv.run()
        while 1:
            console_input = input().strip()
            if console_input[0] == 'q':
                selection = input(CONSOLE_ALIAS + " Are you sure you want to quit?(Y/n): ")
                if selection and selection[0] == "Y":
                    print(CONSOLE_ALIAS, man.shutdown_all())
                    print(CONSOLE_ALIAS, "Quiting...")
                    break
                else:
                    continue

            temp_list = split(console_input)
            if len(temp_list) >= 2:
                command = temp_list[0]
                name = temp_list[1]
                if not (name == TERM_ALL or name in man.server_names()):
                    print(f'{CONSOLE_ALIAS} \"{name}\" is not a valid server identity')
                    continue
                message = None
                if len(temp_list) > 2:
                    message = temp_list[2]

                # print(f'c= {command}, n= {name}, m= {message}')
                if command[0] == 'r':
                    if name == TERM_ALL:
                        print(CONSOLE_ALIAS, "Starting all")
                        man.start_all()
                    else:
                        print(f"{CONSOLE_ALIAS} Starting {name}")
                        result = man.start(name)
                elif command[0] == 'c':
                    if name == TERM_ALL:
                        print(CONSOLE_ALIAS, "Closing all")
                        man.shutdown_all()
                    else:
                        print(f"{CONSOLE_ALIAS} Closing {name}")
                        man.shutdown(name)
                elif command[0] == 'w':
                    if message:
                        if name == TERM_ALL:
                            print(f'{CONSOLE_ALIAS} Writing \"{message}\" to all')
                            man.send_all(message)
                        else:
                            print(f'{CONSOLE_ALIAS} Writing \"{message}\" to {name}')
                            man.send(name, message)
                    else:
                        print(CONSOLE_ALIAS, "Please specify a message to write")
                elif command[0] == 's':
                    if name == TERM_ALL:
                        for serv_name in man.server_names():
                            if message == "debug":
                                serv_status = man.status(serv_name, debug=True)
                            else:
                                serv_status = man.status(serv_name)
                            print(f"{CONSOLE_ALIAS} PRINTING STATUS FOR {serv_name}")
                            for key in serv_status.keys():
                                print(f"\t{key}: {serv_status.get(key)}")
                            print()
                    else:
                        if message == "debug":
                            serv_status = man.status(name, debug=True)
                        else:
                            serv_status = man.status(name)
                        print(f"{CONSOLE_ALIAS} PRINTING STATUS FOR {name}")
                        for key in serv_status.keys():
                            print(f">>\t{key}: {serv_status.get(key)}")
                elif command[0] == 'o':
                    if not message:
                        print(CONSOLE_ALIAS, "Please specify a value. (t)rue or (f)alse")
                        continue
                    if name == TERM_ALL:
                        if message[0] == 't':
                            man.output_all(True)
                        elif message == 'f':
                            man.output_all(False)
                        else:
                            print(CONSOLE_ALIAS, "value must be (t)rue or (f)alse")
                            continue
                    else:
                        if message[0] == 't':
                            man.output(name, True)
                        elif message == 'f':
                            man.output(name, False)
                        else:
                            print(CONSOLE_ALIAS, "value must be (t)rue or (f)alse")
                            continue

                else:
                    print(CONSOLE_ALIAS, "INVALID COMMAND")
            else:
                print(CONSOLE_ALIAS, "INVALID INPUT")
                print(CONSOLE_ALIAS, "<command> <server name> <message>")

    finally:
        # serv.shutdown()
        pass
