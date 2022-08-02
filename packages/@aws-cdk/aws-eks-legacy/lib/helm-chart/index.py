import subprocess
import os
import json
import logging
import boto3
from uuid import uuid4
from botocore.vendored import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# these are coming from the kubectl layer
os.environ['PATH'] = '/opt/helm:/opt/awscli:' + os.environ['PATH']

outdir = os.environ.get('TEST_OUTDIR', '/tmp')
kubeconfig = os.path.join(outdir, 'kubeconfig')

CFN_SUCCESS = "SUCCESS"
CFN_FAILED = "FAILED"

def handler(event, context):

    def cfn_error(message=None):
        logger.error(f"| cfn_error: {message}")
        cfn_send(event, context, CFN_FAILED, reason=message)

    try:
        logger.info(json.dumps(event))

        request_type = event['RequestType']
        props = event['ResourceProperties']
        physical_id = event.get('PhysicalResourceId', None)
        release = props['Release']
        chart = props['Chart']
        version = props.get('Version', None)
        namespace = props.get('Namespace', None)
        repository = props.get('Repository', None)
        values_text = props.get('Values', None)

        cluster_name = os.environ.get('CLUSTER_NAME', None)
        if cluster_name is None:
            cfn_error("CLUSTER_NAME is missing in environment")
            return

        subprocess.check_call([ 'aws', 'eks', 'update-kubeconfig',
            '--name', cluster_name,
            '--kubeconfig', kubeconfig
        ])

        # Write out the values to a file and include them with the install and upgrade
        values_file = None
        if request_type != "Delete" and values_text is not None:
            values = json.loads(values_text)
            values_file = os.path.join(outdir, 'values.yaml')
            with open(values_file, "w") as f:
                f.write(json.dumps(values, indent=2))

        if request_type in ['Create', 'Update']:
            helm('upgrade', release, chart, repository, values_file, namespace, version)
        elif request_type == "Delete":
            try:
                helm('uninstall', release, namespace=namespace)
            except Exception as e:
                logger.info(f"delete error: {e}")

        # if we are creating a new resource, allocate a physical id for it
        # otherwise, we expect physical id to be relayed by cloudformation
        if request_type == 'Create':
            physical_id = f"{cluster_name}/{str(uuid4())}"
        else:
            if not physical_id:
                cfn_error("invalid request: request type is '%s' but 'PhysicalResourceId' is not defined" % request_type)
                return

        cfn_send(event, context, CFN_SUCCESS, physicalResourceId=physical_id)
        return

    except KeyError as e:
        cfn_error("invalid request. Missing '%s'" % str(e))
    except Exception as e:
        logger.exception(e)
        cfn_error(str(e))

def helm(verb, release, chart = None, repo = None, file = None, namespace = None, version = None):
    import subprocess
    try:
        cmnd = ['helm', verb, release]
        if chart is not None:
            cmnd.append(chart)
        if verb == 'upgrade':
            cmnd.append('--install')
        if repo is not None:
            cmnd.extend(['--repo', repo])
        if file is not None:
            cmnd.extend(['--values', file])
        if version is not None:
            cmnd.extend(['--version', version])
        if namespace is not None:
            cmnd.extend(['--namespace', namespace])
        cmnd.extend(['--kubeconfig', kubeconfig])
        output = subprocess.check_output(cmnd, stderr=subprocess.STDOUT, cwd=outdir)
        logger.info(output)
    except subprocess.CalledProcessError as exc:
        raise Exception(exc.output)

#---------------------------------------------------------------------------------------------------
# sends a response to cloudformation
def cfn_send(event, context, responseStatus, responseData={}, physicalResourceId=None, noEcho=False, reason=None):

    responseUrl = event['ResponseURL']
    logger.info(responseUrl)

    responseBody = {
        'Status': responseStatus,
        'Reason': reason
        or f'See the details in CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': physicalResourceId or context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'NoEcho': noEcho,
        'Data': responseData,
    }

    body = json.dumps(responseBody)
    logger.info("| response body:\n" + body)

    headers = {
        'content-type' : '',
        'content-length' : str(len(body))
    }

    try:
        response = requests.put(responseUrl, data=body, headers=headers)
        logger.info(f"| status code: {response.reason}")
    except Exception as e:
        logger.error("| unable to send response to CloudFormation")
        logger.exception(e)
