import time
import docker
import glob
from src.config_files.constants import *
from src.libs import utils
from src.libs import curated_apps_lib
from conftest import trd

class RedisWorkload:
    def __init__(self, test_config_dict):
        self.server_ip_addr = utils.determine_host_ip_addr()

    def pull_workload_default_image(self, test_config_dict):
        try:
            workload_docker_image_name = utils.get_workload_name(test_config_dict['docker_image'])
            workload_docker_pull_cmd = f"docker pull {workload_docker_image_name}"
            print(f"\n-- Pulling latest redis docker image from docker hub..\n", workload_docker_pull_cmd)
            utils.exec_shell_cmd(workload_docker_pull_cmd, None)
        except (docker.errors.ImageNotFound, docker.errors.APIError):
            raise Exception(f"\n-- Docker pull for image {workload_docker_image_name} failed!!")

    def update_server_details_in_client(self, tcd):
        client_name = tcd['client_username'] + "@" + tcd['client_ip']
        client_file_path = os.path.join(tcd['client_scripts_path'], "instance_benchmark.sh")

        # Setting 'SSHPASS' env variable for ssh commands
        print(f"\n-- Updating 'SSHPASS' env-var\n")
        os.environ['SSHPASS'] = "intel@123"

        # Updating Server IP.
        host_sed_cmd = f"sed -i 's/^export HOST.*/export HOST=\"{self.server_ip_addr}\"/' {client_file_path}"
        update_server_ip_cmd = f"sshpass -e ssh {client_name} \"{host_sed_cmd}\""
        print("\n-- Updating server IP within redis client script..")
        print(update_server_ip_cmd)
        utils.exec_shell_cmd(update_server_ip_cmd)

        # Updating Server Port.
        port_sed_cmd = f"sed -i 's/^export MASTER_START_PORT.*/export MASTER_START_PORT=\"{str(tcd['server_port'])}\"/' {client_file_path}"
        update_server_port_cmd = f"sshpass -e ssh {client_name} \"{port_sed_cmd}\""
        print("\n-- Updating server Port within redis client script..")
        print(update_server_port_cmd)
        utils.exec_shell_cmd(update_server_port_cmd)

    def pre_actions(self, test_config_dict):
        #utils.set_threads_cnt_env_var()
        utils.set_cpu_freq_scaling_governor()
        self.update_server_details_in_client(test_config_dict)

    def setup_workload(self, test_config_dict):
        self.pull_workload_default_image(test_config_dict)

    def construct_server_workload_exec_cmd(self, test_config_dict, exec_mode = 'native'):
        redis_exec_cmd = None

        server_size = test_config_dict['server_size'] * 1024 * 1024 * 1024
        exec_bin_str = './redis-server' if exec_mode == 'native' else 'redis-server'

        tmp_exec_cmd = f"{exec_bin_str} --port {test_config_dict['server_port']} --maxmemory {server_size} --maxmemory-policy allkeys-lru --appendonly no --protected-mode no --save '' &"
        
        if exec_mode == 'native':
            redis_exec_cmd = "numactl -C 1 " + tmp_exec_cmd
        elif exec_mode == 'gramine-direct':
            redis_exec_cmd = "numactl -C 1 gramine-direct " + tmp_exec_cmd
        elif exec_mode == 'gramine-sgx':
            redis_exec_cmd = "numactl -C 1,2 gramine-sgx " + tmp_exec_cmd
        else:
            raise Exception(f"\nInvalid execution mode specified in config yaml!")

        print("\n-- Server command name = \n", redis_exec_cmd)
        return redis_exec_cmd

    def construct_client_exec_cmd(self, tcd, exec_mode = 'native'):
        client_ssh_cmd = None
        client_name = tcd['client_username'] + "@" + tcd['client_ip']
        benchmark_exec_mode = 'native'

        if exec_mode == 'gramine-direct':
            benchmark_exec_mode = 'graphene'
        elif exec_mode == 'gramine-sgx':
            benchmark_exec_mode = 'graphene_sgx_diff_core'

        benchmark_exec_cmd = f"cd {tcd['client_scripts_path']} && ./start_benchmark.sh {benchmark_exec_mode} {tcd['test_name']} {tcd['data_size']} {tcd['rw_ratio']} {tcd['iterations']}"
        client_ssh_cmd = f"sshpass -e ssh {client_name} '{benchmark_exec_cmd}'"

        print("\n-- Client command name = \n", client_ssh_cmd)

        return client_ssh_cmd

    def free_redis_server_port(self, tcd, e_mode):
        container_id = None
        workload_docker_image_name = utils.get_workload_name(tcd['docker_image'])
        print(f"\n-- Killing docker container with image name: {workload_docker_image_name} to free the server port..")
        if e_mode == 'gramine-sgx':
            workload_docker_image_name = "gsc-" + workload_docker_image_name + "x"
        docker_container_list = utils.exec_shell_cmd("docker ps").splitlines()
        for container_item in docker_container_list:
            if container_item.split()[1] == workload_docker_image_name:
                container_id = container_item.split()[0]
                break
        
        if container_id is None:
            raise Exception(f"\n-- Could not find Container ID for {workload_docker_image_name}")
        
        docker_kill_cmd = f"docker kill {container_id}"
        utils.exec_shell_cmd(docker_kill_cmd)

    def parse_csv_res_files(self, tcd):
        csv_test_res_folder = os.path.join(PERF_RESULTS_DIR, tcd['workload_name'], tcd['test_name'])
        os.chdir(csv_test_res_folder)
        csv_files = glob.glob1(csv_test_res_folder, "*.csv")
        
        if len(csv_files) != (len(tcd['exec_mode']) * tcd['iterations']):
            raise Exception(f"\n-- Number of test result files - {len(csv_files)} is not equal to the expected number - {len(tcd['exec_mode']) * tcd['iterations']}.\n")

        global trd
        test_dict_throughput = {}
        test_dict_latency = {}
        for e_mode in tcd['exec_mode']:
            test_dict_throughput[e_mode] = []
            test_dict_latency[e_mode] = []
        
        avg_latency = 0
        avg_throughput = 0
        for filename in csv_files:
            with open(filename, "r") as f:
                for row in f.readlines():
                    row = row.split()
                    if row:
                        if "Totals" in row[0]:
                            avg_latency = row[5]
                            avg_throughput = row[-1]
                            break

                if "native" in filename:
                    test_dict_latency['native'].append(float(avg_latency))
                    test_dict_throughput['native'].append(float(avg_throughput))
                elif "graphene_sgx" in filename:
                    test_dict_latency['gramine-sgx'].append(float(avg_latency))
                    test_dict_throughput['gramine-sgx'].append(float(avg_throughput))
                else:
                    test_dict_latency['gramine-direct'].append(float(avg_latency))
                    test_dict_throughput['gramine-direct'].append(float(avg_throughput))

        if 'native' in tcd['exec_mode']:
            test_dict_latency['native-avg'] = '{:0.3f}'.format(sum(test_dict_latency['native'])/len(test_dict_latency['native']))
            test_dict_throughput['native-avg'] = '{:0.3f}'.format(sum(test_dict_throughput['native'])/len(test_dict_throughput['native']))

        if 'gramine-direct' in tcd['exec_mode']:
            test_dict_latency['direct-avg'] = '{:0.3f}'.format(
                sum(test_dict_latency['gramine-direct'])/len(test_dict_latency['gramine-direct']))
            test_dict_throughput['direct-avg'] = '{:0.3f}'.format(
                sum(test_dict_throughput['gramine-direct'])/len(test_dict_throughput['gramine-direct']))
            if 'native' in tcd['exec_mode']:
                test_dict_latency['direct-deg'] = utils.percent_degradation(test_dict_latency['native-avg'], test_dict_latency['direct-avg'])
                test_dict_throughput['direct-deg'] = utils.percent_degradation(test_dict_throughput['native-avg'], test_dict_throughput['direct-avg'])

        if 'gramine-sgx' in tcd['exec_mode']:
            test_dict_latency['sgx-avg'] = '{:0.3f}'.format(sum(test_dict_latency['gramine-sgx'])/len(test_dict_latency['gramine-sgx']))
            test_dict_throughput['sgx-avg'] = '{:0.3f}'.format(sum(test_dict_throughput['gramine-sgx'])/len(test_dict_throughput['gramine-sgx']))
            if 'native' in tcd['exec_mode']:
                test_dict_latency['sgx-deg'] = utils.percent_degradation(test_dict_latency['native-avg'], test_dict_latency['sgx-avg'])
                test_dict_throughput['sgx-deg'] = utils.percent_degradation(test_dict_throughput['native-avg'], test_dict_throughput['sgx-avg'])

        trd[tcd['workload_name']] = trd.get(tcd['workload_name'], {})
        trd[tcd['workload_name']].update({tcd['test_name']+'_latency': test_dict_latency})
        trd[tcd['workload_name']].update({tcd['test_name']+'_throughput': test_dict_throughput})

        os.chdir(FRAMEWORK_HOME_DIR)

    def process_results(self, tcd):
        csv_res_folder = os.path.join(PERF_RESULTS_DIR, tcd['workload_name'])
        if not os.path.exists(csv_res_folder): os.makedirs(csv_res_folder)

        # Copy test results folder from client to local server results folder.
        client_res_folder = os.path.join(tcd['client_results_path'], tcd['test_name'])
        client_scp_path = tcd['client_username'] + "@" + tcd['client_ip'] + ":" + client_res_folder
        copy_client_to_server_cmd = f"sshpass -e scp -r {client_scp_path} {csv_res_folder}"
        utils.exec_shell_cmd(copy_client_to_server_cmd)

        # Parse the individual csv result files and update the global test results dict.
        self.parse_csv_res_files(tcd)

    # Build the workload execution command based on execution params and execute it.
    def execute_workload(self, tcd):
        print("\n##### In execute_workload #####\n")

        print("\n-- Deleting older test results from client..")
        test_res_path = os.path.join(tcd['client_results_path'], tcd['test_name'])
        client_name = tcd['client_username'] + "@" + tcd['client_ip']
        test_res_del_cmd = f"sshpass -e ssh {client_name} 'rm -rf {test_res_path}'"
        print(test_res_del_cmd)
        utils.exec_shell_cmd(test_res_del_cmd)

        for e_mode in tcd['exec_mode']:
            print(f"\n-- Executing {tcd['test_name']} in {e_mode} mode")

            if e_mode == 'native':
                # Bring up the native redis server.
                workload_docker_image_name = utils.get_workload_name(tcd['docker_image'])
                docker_native_cmd = f"docker run --rm --net=host -t {workload_docker_image_name} &"
                utils.exec_shell_cmd(docker_native_cmd, None)
            else:
                # Graminized redis server invocation (SGX execution) using curated_apps_lib.
                curated_apps_lib.run_test(tcd)
            
            time.sleep(5)

            # Construct and execute memtier benchmark command within client.
            client_ssh_cmd = self.construct_client_exec_cmd(tcd, e_mode)
            utils.exec_shell_cmd(client_ssh_cmd, None)
            
            time.sleep(5)

            self.free_redis_server_port(tcd, e_mode)
            if 'gramine-sgx' in tcd['exec_mode']:
                workload_docker_image_name = utils.get_workload_name(tcd['docker_image'])
                utils.cleanup_after_test(workload_docker_image_name)

            time.sleep(TEST_SLEEP_TIME_BW_ITERATIONS)
        
        self.process_results(tcd)
