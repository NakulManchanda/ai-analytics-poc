# AWS console links

Terraform was applied on 2026-08-18 in AWS account `107207236011`, Region
`us-east-1`. Confirm that the signed-in AWS Console account is
`107207236011` before opening these links.

## Networking

- [VPC](https://us-east-1.console.aws.amazon.com/vpc/home?region=us-east-1#vpcs:VpcId=vpc-052d141d505c54b93)
- [Public subnet 1](https://us-east-1.console.aws.amazon.com/vpc/home?region=us-east-1#subnets:subnetId=subnet-0ff069852e48b7aae)
- [Public subnet 2](https://us-east-1.console.aws.amazon.com/vpc/home?region=us-east-1#subnets:subnetId=subnet-0c6da6c5ddcf97418)
- [Private subnet 1](https://us-east-1.console.aws.amazon.com/vpc/home?region=us-east-1#subnets:subnetId=subnet-09b92ec8f00b31736)
- [Private subnet 2](https://us-east-1.console.aws.amazon.com/vpc/home?region=us-east-1#subnets:subnetId=subnet-0632d48cc0a21fc45)
- [ECS task security group](https://us-east-1.console.aws.amazon.com/ec2/home?region=us-east-1#SecurityGroup:groupId=sg-0a3dc0d05c71937cb)

## Container platform

- [ai-app ECR repository](https://us-east-1.console.aws.amazon.com/ecr/repositories/private/ai-analytics-poc-demo-ai-app?region=us-east-1)
- [analytics-mcp ECR repository](https://us-east-1.console.aws.amazon.com/ecr/repositories/private/ai-analytics-poc-demo-analytics-mcp?region=us-east-1)
- [ECS cluster](https://us-east-1.console.aws.amazon.com/ecs/v2/clusters/ai-analytics-poc-demo-cluster/services?region=us-east-1)

## Storage and state

- [Frontend bucket](https://s3.console.aws.amazon.com/s3/buckets/ai-analytics-poc-demo-107207236011-us-east-1-frontend?region=us-east-1&tab=objects)
- [Artifact bucket](https://s3.console.aws.amazon.com/s3/buckets/ai-analytics-poc-demo-107207236011-us-east-1-artifacts?region=us-east-1&tab=objects)
- [Application-state DynamoDB table](https://us-east-1.console.aws.amazon.com/dynamodbv2/home?region=us-east-1#table?name=ai-analytics-poc-demo-application-state)

## IAM roles

- [ECS task execution role](https://console.aws.amazon.com/iam/home#/roles/details/ai-analytics-poc-demo-ecs-task-execution)
- [ai-app task role](https://console.aws.amazon.com/iam/home#/roles/details/ai-analytics-poc-demo-ai-app-task)
- [analytics-mcp task role](https://console.aws.amazon.com/iam/home#/roles/details/ai-analytics-poc-demo-analytics-mcp-task)

## CloudWatch logs

- [ai-app log group](https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#logsV2:log-groups/log-group/%2Fecs%2Fai-analytics-poc-demo-ai-app)
- [analytics-mcp log group](https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#logsV2:log-groups/log-group/%2Fecs%2Fai-analytics-poc-demo-analytics-mcp)

## Refreshing the links

Run from the repository root after a later Terraform apply:

```bash
AWS_PROFILE=default terraform -chdir=infra/terraform output \
  -state=/Users/nakulmanchanda/.local/state/ai-analytics-poc/terraform.tfstate \
  -json console_links
```

The command reads the explicit external state file and prints the current
`console_links` object; it does not apply infrastructure changes.
