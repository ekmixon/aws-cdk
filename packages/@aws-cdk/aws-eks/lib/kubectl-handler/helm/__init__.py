import json
import logging
import os
import subprocess

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# these are coming from the kubectl layer
os.environ['PATH'] = '/opt/helm:/opt/awscli:' + os.environ['PATH']

outdir = os.environ.get('TEST_OUTDIR', '/tmp')
kubeconfig = os.path.join(outdir, 'kubeconfig')


def helm_handler(event, context):
    logger.info(json.dumps(event))

    request_type = event['RequestType']
    props = event['ResourceProperties']

    # resource properties
    cluster_name = props['ClusterName']
    role_arn     = props['RoleArn']
    release      = props['Release']
    chart        = props['Chart']
    version      = props.get('Version', None)
    wait         = props.get('Wait', False)
    timeout      = props.get('Timeout', None)
    namespace    = props.get('Namespace', None)
    create_namespace = props.get('CreateNamespace', None)
    repository   = props.get('Repository', None)
    values_text  = props.get('Values', None)

    # "log in" to the cluster
    subprocess.check_call([ 'aws', 'eks', 'update-kubeconfig',
        '--role-arn', role_arn,
        '--name', cluster_name,
        '--kubeconfig', kubeconfig
    ])

    if os.path.isfile(kubeconfig):
        os.chmod(kubeconfig, 0o600)

    # Write out the values to a file and include them with the install and upgrade
    values_file = None
    if request_type != "Delete" and values_text is not None:
        values = json.loads(values_text)
        values_file = os.path.join(outdir, 'values.yaml')
        with open(values_file, "w") as f:
            f.write(json.dumps(values, indent=2))

    if request_type in ['Create', 'Update']:
        helm('upgrade', release, chart, repository, values_file, namespace, version, wait, timeout, create_namespace)
    elif request_type == "Delete":
        try:
            helm('uninstall', release, namespace=namespace, timeout=timeout)
        except Exception as e:
            logger.info(f"delete error: {e}")

def helm(verb, release, chart = None, repo = None, file = None, namespace = None, version = None, wait = False, timeout = None, create_namespace = None):
    import subprocess

    cmnd = ['helm', verb, release]
    if chart is not None:
        cmnd.append(chart)
    if verb == 'upgrade':
        cmnd.append('--install')
    if create_namespace:
        cmnd.append('--create-namespace')
    if repo is not None:
        cmnd.extend(['--repo', repo])
    if file is not None:
        cmnd.extend(['--values', file])
    if version is not None:
        cmnd.extend(['--version', version])
    if namespace is not None:
        cmnd.extend(['--namespace', namespace])
    if wait:
        cmnd.append('--wait')
    if timeout is not None:
        cmnd.extend(['--timeout', timeout])
    cmnd.extend(['--kubeconfig', kubeconfig])

    maxAttempts = 3
    retry = maxAttempts
    while retry > 0:
        try:
            output = subprocess.check_output(cmnd, stderr=subprocess.STDOUT, cwd=outdir)
            logger.info(output)
            return
        except subprocess.CalledProcessError as exc:
            output = exc.output
            if b'Broken pipe' not in output:
                raise Exception(output)
            retry -= 1
            logger.info(f"Broken pipe, retries left: {retry}")
    raise Exception(f'Operation failed after {maxAttempts} attempts: {output}')
