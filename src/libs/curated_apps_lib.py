import re
import sys
from src.libs import utils
from src.config_files.constants import *


def dcap_setup():
    copy_cmd = "cp /etc/sgx_default_qcnl.conf {}/verifier_image/".format(os.path.join(ORIG_CURATED_PATH, CURATED_PATH))
    utils.run_subprocess(copy_cmd)
    fd = open(VERIFIER_DOCKERFILE)
    fd_contents = fd.read()
    azure_dcap = "(.*)RUN wget https:\/\/packages.microsoft(.*)\n(.*)amd64.deb"
    updated_content = re.sub(azure_dcap, "", fd_contents)
    dcap_library = "RUN apt-get install -y gramine-dcap\nRUN apt install -y libsgx-dcap-default-qpl libsgx-dcap-default-qpl-dev\nCOPY sgx_default_qcnl.conf  /etc/sgx_default_qcnl.conf"
    new_data = re.sub("RUN apt-get install -y gramine-dcap", dcap_library, updated_content)
    fd.close()

    fd = open(VERIFIER_DOCKERFILE, "w+")
    fd.write(new_data)
    fd.close()


def curated_setup():
    print("Cleaning old contrib repo")
    rm_cmd = "rm -rf {}".format(ORIG_CURATED_PATH)
    utils.exec_shell_cmd(rm_cmd)
    print("Cloning and checking out Contrib Git Repo")
    utils.exec_shell_cmd(CONTRIB_GIT_CMD)
    # utils.exec_shell_cmd(GIT_CHECKOUT_CMD)
    if utils.check_machine() == "DCAP client":
        print("Configuring the contrib repo to setup DCAP client")
        dcap_setup()


def copy_repo():
    copy_cmd = "cp -rf {} {}".format(ORIG_CURATED_PATH, REPO_PATH)
    utils.exec_shell_cmd("rm -rf contrib_repo")
    utils.exec_shell_cmd(copy_cmd)


def generate_curated_image(test_config_dict):
    curation_output = ''
    workload_image = test_config_dict["docker_image"]

    curation_cmd = 'python3 curation_app.py ' + workload_image + ' test'
    
    print("Curation cmd ", curation_cmd)
    process = utils.popen_subprocess(curation_cmd, CURATED_APPS_PATH)

    while True:
        output = process.stdout.readline()
        if process.poll() is not None and output == '':
            break
        if output:
            print(output.strip())
            curation_output += output
            # if "docker run" in output:
            #     curation_output = True
            #     break
    return curation_output


def get_docker_run_command(workload_name):
    output = []
    wrapper_image = "gsc-{}x".format(workload_name)
    gsc_workload = "docker run --rm --net=host --device=/dev/sgx/enclave -t {}".format(wrapper_image)
    output.append(gsc_workload)
    return output


def run_curated_image(docker_run_cmd):
    result = False
    pytorch_result = ["Result", "Labrador retriever", "golden retriever", "Saluki, gazelle hound", "whippet", "Ibizan hound, Ibizan Podenco"]
    gsc_docker_command = docker_run_cmd[-1]

    process = utils.popen_subprocess(gsc_docker_command)
    while True:
        nextline = process.stdout.readline()
        print(nextline.strip())
        if nextline == '' and process.poll() is not None:
            break
        if "Ready to accept connections" in nextline or all(x in nextline for x in pytorch_result):
            process.stdout.close()
            utils.kill(process.pid)
            sys.stdout.flush()
            result = True
            break
    return result


def run_test(test_config_dict):
    result = False
    
    # try:
    workload_name = utils.get_workload_name(test_config_dict['docker_image'])
    curation_output = generate_curated_image(test_config_dict)

    if curation_output:
        docker_run_cmd = get_docker_run_command(workload_name)
        result = run_curated_image(docker_run_cmd)
            # if "redis" in test_name:
            #     result = workload.run_redis_client()
    # finally:
    #     print("Docker images cleanup")
    #     utils.cleanup_after_test(workload_name)
    return result
