"""Build a credential-free CloudFormation template. No deployment side effects."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ref = lambda name: {'Ref': name}
arn = lambda name: {'Fn::GetAtt': [name, 'Arn']}
sub = lambda text: {'Fn::Sub': text}

def role(service, statements, conditions=None):
    trust = {'Effect': 'Allow', 'Principal': {'Service': service}, 'Action': 'sts:AssumeRole'}
    if conditions:
        trust['Condition'] = conditions
    return {'Type': 'AWS::IAM::Role', 'Properties': {
        'AssumeRolePolicyDocument': {'Version': '2012-10-17', 'Statement': [trust]},
        'Policies': [{'PolicyName': 'CollectorOnly', 'PolicyDocument': {'Version': '2012-10-17', 'Statement': statements}}]}}

resources = {
    'Archive': {'Type': 'AWS::DynamoDB::Table', 'DeletionPolicy': 'Retain', 'UpdateReplacePolicy': 'Retain', 'Properties': {
        'BillingMode': 'PROVISIONED', 'ProvisionedThroughput': {'ReadCapacityUnits': 5, 'WriteCapacityUnits': 10},
        'AttributeDefinitions': [{'AttributeName': n, 'AttributeType': 'S'} for n in ('pk', 'sk')],
        'KeySchema': [{'AttributeName': 'pk', 'KeyType': 'HASH'}, {'AttributeName': 'sk', 'KeyType': 'RANGE'}],
        'SSESpecification': {'SSEEnabled': True}}},
    'Logs': {'Type': 'AWS::Logs::LogGroup', 'Properties': {'LogGroupName': sub('/aws/lambda/${AWS::StackName}-collector'), 'RetentionInDays': 7}},
    'CollectorRole': role('lambda.amazonaws.com', [
        {'Effect': 'Allow', 'Action': ['dynamodb:GetItem', 'dynamodb:PutItem'], 'Resource': arn('Archive')},
        {'Effect': 'Allow', 'Action': 'ssm:GetParameter', 'Resource': sub('arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter${TokenParameter}')},
        {'Effect': 'Allow', 'Action': ['logs:CreateLogStream', 'logs:PutLogEvents'], 'Resource': arn('Logs')},
    ]),
    'Collector': {'Type': 'AWS::Lambda::Function', 'Properties': {
        'FunctionName': sub('${AWS::StackName}-collector'), 'Runtime': 'python3.12', 'Handler': 'index.handler',
        'Role': arn('CollectorRole'), 'MemorySize': 512, 'Timeout': 240, 'ReservedConcurrentExecutions': 1,
        'Code': {'ZipFile': (ROOT / 'collector.py').read_text(encoding='utf-8')},
        'Environment': {'Variables': {'TABLE_NAME': ref('Archive'), 'TOKEN_PARAMETER': ref('TokenParameter'), 'STOP_DATE': ref('StopDate')}}}},
    'ScheduleGroup': {'Type': 'AWS::Scheduler::ScheduleGroup'},
    'SchedulerRole': role('scheduler.amazonaws.com', [
        {'Effect': 'Allow', 'Action': 'lambda:InvokeFunction', 'Resource': arn('Collector')}
    ], {'StringEquals': {'aws:SourceAccount': ref('AWS::AccountId')}, 'ArnEquals': {'aws:SourceArn': arn('ScheduleGroup')}}),
    'InvokeConfig': {'Type': 'AWS::Lambda::EventInvokeConfig', 'Properties': {
        'FunctionName': ref('Collector'), 'Qualifier': '$LATEST', 'MaximumEventAgeInSeconds': 120, 'MaximumRetryAttempts': 0}},
}
for name, cron, mode in [('MarketSchedule', 'cron(0/5 9-15 ? * MON-FRI *)', 'snapshot'), ('ClosingSchedule', 'cron(0 16 ? * MON-FRI *)', 'snapshot'), ('EodSchedule', 'cron(0,30 17,19 ? * MON-FRI *)', 'eod')]:
    resources[name] = {'Type': 'AWS::Scheduler::Schedule', 'Properties': {
        'GroupName': ref('ScheduleGroup'), 'ScheduleExpression': cron, 'ScheduleExpressionTimezone': 'Asia/Kolkata',
        'FlexibleTimeWindow': {'Mode': 'OFF'}, 'State': ref('ScheduleState'), 'EndDate': sub('${StopDate}T00:00:00Z'),
        'Target': {'Arn': arn('Collector'), 'RoleArn': arn('SchedulerRole'), 'Input': json.dumps({'mode': mode}),
                   'RetryPolicy': {'MaximumEventAgeInSeconds': 60, 'MaximumRetryAttempts': 0}}}}
template = {
    'AWSTemplateFormatVersion': '2010-09-09',
    'Description': 'Volatility Lab private read-only Upstox collector. Keep account on Free Plan; no automatic upgrades.',
    'Parameters': {
        'TokenParameter': {'Type': 'String', 'Default': '/volatility-lab/upstox-analytics', 'AllowedPattern': '/[a-zA-Z0-9_./-]+', 'Description': 'Existing SSM Standard SecureString; NEVER paste the token in this field.'},
        'StopDate': {'Type': 'String', 'Default': '2027-04-09', 'AllowedPattern': '[0-9]{4}-[0-9]{2}-[0-9]{2}', 'Description': 'Stop before this account Free Plan ends on 2027-04-16; export data earlier.'},
        'ScheduleState': {'Type': 'String', 'Default': 'DISABLED', 'AllowedValues': ['DISABLED', 'ENABLED'], 'Description': 'Enable only after successful live capture and download verification.'},
    },
    'Resources': resources,
    'Outputs': {'TableName': {'Value': ref('Archive')}, 'CollectorFunction': {'Value': ref('Collector')}, 'TokenParameter': {'Value': ref('TokenParameter')}, 'ScheduledStop': {'Value': ref('StopDate')}},
}
(ROOT / 'cloudformation.json').write_text(json.dumps(template, indent=2), encoding='utf-8')
print('Built cloudformation.json; schedules disabled; no credentials included.')
