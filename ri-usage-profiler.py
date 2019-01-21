# -*- coding: utf-8 -*-
# AWS中国区RI使用状态分析器
# By: Hanawana(me@hanawana.name) 2018-12-24
# Last update: 2019-1-17

from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import argparse
import re
import json

# 字典表：用于从DBR Operation字段中获取操作系统信息
# 注：platform的名称与RI订阅记录里的ProductDescription保持一致，以防匹配错误
# TODO 移入配置文件单独维护
# TODO 补充更多的RunInstance类型
dict_operation_platform = {
    'RunInstances': 'Linux/UNIX',
    'RunInstances:0002': 'Windows',
    'RunInstances:0010': 'Red Hat Enterprise Linux',
}

# 字典表：用于计算Linux/UNIX instance的标准化值的标准化系数表
# 注：标准化系数以及在RI计算中的应用参见 https://docs.aws.amazon.com/zh_cn/AWSEC2/latest/UserGuide/apply_ri.html
# TODO 移入配置文件单独维护
dict_nf = {
    'nano': 0.25,
    'micro': 0.5,
    'small': 1,
    'medium': 2,
    'large': 4,
    'xlarge': 8,
    '2xlarge': 16,
    '4xlarge': 32,
    '8xlarge': 64,
    '9xlarge': 72,
    '10xlarge': 80,
    '12xlarge': 96,
    '16xlarge': 128,
    '18xlarge': 144,
    '24xlarge': 192,
    '32xlarge': 256,
}

# 命令行参数处理
def parse_args():
    parser = argparse.ArgumentParser(description='Get AWS EC2 RI coverage')
    parser.add_argument('-d', dest='dbr_file', required=True,
                        help="Path of your DBR file, zipped CSV format only")
    parser.add_argument('-s', dest='ri_sub_file', required=True,
                        help="Path of your RI subscription json file")
    parser.add_argument('-t', dest='date_hour', required=True,
                        help="Specified hour to calulate, say 2018-10-02/11 means 2018-10-02 11:00")
    args = parser.parse_args()
    return args.dbr_file, args.ri_sub_file, args.date_hour


# 派生列生成器：生成EC2的维度及度量列
# RI使用情况分析常见的维度和度量计算规则：
# 1、维度：EC2Model以及它的EC2Family和EC2Size两个子维度
#    从UsageType中获取EC2的Model, Family & Size
#    e.g  CNN1-HeavyUsage:c4.2xlarge => c4.2xlarge, c4, 2xlarge
# 2、维度：EC2Platform
#    从Operation字段里获取，用于跟RI订阅记录里的ProductDescription属性匹配
# 3、维度：EC2RIModel
#    出现过一个EC2被部分或者多条RI订阅匹配的情况（见下文4中的注），因此计算RI占用应该用匹配上的那个RI的Model而不是EC2Model，从ItemDescription中取
#    注：ItemDescription形如：  CNY 0.0 per Linux/UNIX (Amazon VPC), c4.4xlarge reserved instance applied
# 4、度量：RI的标准化值NP
#    1) 对Linux/UNIX型的instance计算标准化值
#    2) 对其他类型的实例，一律为 1
#    3) 为了日后聚合计算方便，采用负值
#    4) 非RI的，这项值为 0
#    注：
#      出现过一个instance在同一小时有两条记录的例子，比较罕见，曾经出现过：
#          - 两条记录一条RI=Y一条为N，是部分覆盖情况，两者的UsageQuantity加起来为1
#          - 是两条记录RI都为Y，是RI叠加情况做到全覆盖的情况，动用了0.75个c4.xlarge + 0.25个c4.4xlarge
#          - 从原理上看可能存在同一小时出现更多条记录的情况
#      影响：
#      1、按照目前的计算规则不会影响RI的标准化值度量计算，但其他跟EC2数量有关的计算，需要考虑可能带来的影响
#      2、只有EC2Model维度是不够的，需要一个新维度 EC2RIModel（见上文）才能正确计算覆盖
def col_gen_ec2_dim(row):
    ec2_model = row['UsageType'][row['UsageType'].index(':') + 1:]  # ec2 model，形如 m4.xlarge
    ec2_model_parts = ec2_model.split('.')
    ec2_model_size = ec2_model_parts[1]
    ec2_ri_model = ec2_model  # 除了4中的描述的特殊情况，这两者应该是一样的
    ec2_platform = dict_operation_platform.get(row['Operation'])
    if (row['ReservedInstance'] == 'Y'):
        # RI计算用的model size 从ItemDescription中取，鉴于内容比较固定，因此使用了比较宽松的正则表达式
        search_obj = re.search(r'[a-z]\d[a-z].\w*', row['ItemDescription'])
        if search_obj:
            ec2_ri_model = search_obj.group(0)
            ec2_ri_model_parts = ec2_ri_model.split('.')
            ec2_ri_model_size = ec2_ri_model_parts[1]
        else:  # Q&D：目前未发现无法匹配的情况
            ec2_ri_model_size = ec2_model_size
        # 如果是linux/Unix，标准化值 = 对应RIModelSize的标准化系数 * 用量，其他则为 1
        nf = dict_nf[ec2_ri_model_size] * row['UsageQuantity'] if ec2_platform == 'Linux/UNIX' else 1
    else:
        # 非RI覆盖的，标准化值 一律设为0
        nf = 0
    # 本项目的重点是RI计算，只输出了EC2Platform, EC2RIModel这两个维度和NP这个度量
    # trick: 为了以后利用pandas的groupby运算，返回时做以下调整
    # 1）标准化值取负数返回 -nf
    # 2）对于Linux/UNIX型的机器，区分机型在这里没有意义，ec2_ri_model规格一律设置为ignored
    return pd.Series([ec2_platform, ec2_ri_model if ec2_platform != 'Linux/UNIX' else 'ignored', -nf])


# 从DBR数据文件生成EC2使用数据集
def create_ec2_dataset(dbr_file, date_hour):
    # 加载DBR数据，形成初始数据集
    cols_needed = ['LinkedAccountId', 'ProductName', 'UsageType', 'Operation', 'UsageStartDate', 'UsageEndDate', 
                   'ItemDescription', 'AvailabilityZone', 'ReservedInstance', 'ResourceId',  'UsageQuantity']
    # 由于DBR数据的特点（字段可枚举、重复度高），采用category类型可以大幅度减少内存消耗，实测大约只有原来的 1/20-1/40
    cols_dtype = {'LinkedAccountId': 'category', 'ProductName': 'category', 'UsageType': 'category', 'Operation': 'category', 
                'ItemDescription': 'category', 'AvailabilityZone': 'category', 'ReservedInstance': 'category', 
                'ResourceId': 'category', 'UsageQuantity': np.float32}
    cols_date = ['UsageStartDate', 'UsageEndDate']
    df = pd.read_csv(dbr_file, usecols=cols_needed, parse_dates=cols_date, dtype=cols_dtype, compression='zip')
    # 筛选出EC2运行数据集合
    # 因无法在load_csv的时候直接筛选，所以只能加载成DataFrame后再操作
    df1 = df[(df['ProductName'] == 'Amazon Elastic Compute Cloud') & (
        df['UsageType'].str.contains('CNN1-BoxUsage|CNN1-HeavyUsage'))]
    # 获取数据的时间区间
    dod_max, dod_min = df1['UsageStartDate'].max(), df1['UsageStartDate'].min()
    date_hour_spec = datetime.strptime(date_hour, '%Y-%m-%d/%H')
    date_hour_spec_1h_later =  date_hour_spec + timedelta(hours=1) 
    if dod_min <= date_hour_spec <= dod_max:  # 指定时间必须在DBR的数据日期区间里
        print('[{0}] Date hour you provided({1}) in date range of DBR data ({2} - {3})'.format(
            'v', date_hour_spec, dod_min, dod_max))
        # 取出指定的那一个小时的运行数据切片
        # 注：历史DBR中包含汇总数据，因此指定时间是月度的第一个小时情况下如2017-07-07/0时
        #    只判断UsageStartDate会把汇总数据也计算进去，所以必须加时间段判断确保时段为1h
        df1 = df1[(df1['UsageStartDate'] == date_hour_spec) & (
            df1['UsageEndDate'] == date_hour_spec_1h_later)]
        # 生成并返回派生列：EC2Platform, EC2RIModel, EC2NP
        result_df = df1.apply(lambda row: col_gen_ec2_dim(row), axis=1)
        result_df.rename(columns={0:'EC2Platform', 1: 'EC2RIModel', 2: 'EC2NP'}, inplace=True)
        return result_df
    else:
        print('[Failed] Date hour you provided {0} is out of date range of DBR data ({1} - {2})'.format(
            date_hour_spec, dod_min, dod_max))
        return None

# 从订阅数据文件生成EC2 RI订阅信息数据集
def create_ec2_ri_dataset(ri_sub_file, date_hour):
    # load from RI subscription json file
    with open(ri_sub_file, "r") as read_file:
        data = json.load(read_file)
    read_file.close()
    date_hour_spec = datetime.strptime(date_hour, '%Y-%m-%d/%H')
    result = []
    for ri_sub in data['ReservedInstances']:
        # RI订阅使用UTC时间，DBR使用北京时间，所以这里需要+8h修订
        date_start = datetime.strptime(ri_sub['Start'], '%Y-%m-%dT%H:%M:%S.%fZ') + timedelta(hours=8)  
        date_end = datetime.strptime(ri_sub['End'], '%Y-%m-%dT%H:%M:%S.%fZ') + timedelta(hours=8)  
        if (date_start <= date_hour_spec <= date_end): # RI json中的state代表的是导出订阅数据时刻的订阅状态，直接拿来做有效性判断有问题
            ec2_ri_model = ri_sub['InstanceType']
            ec2_ri_model_parts = ec2_ri_model.split('.')
            ec2_ri_model_size = ec2_ri_model_parts[1]
            ec2_ri_platform = ri_sub['ProductDescription']
            np = (dict_nf.get(ec2_ri_model_size, 0) if ec2_ri_platform == 'Linux/UNIX' else 1) * ri_sub['InstanceCount']
            # 对于Linux/UNIX型的机器，区分机型在这里没有意义，ec2_ri_model规格一律设置为ignored
            result.append([ec2_ri_platform , ec2_ri_model if ec2_ri_platform != 'Linux/UNIX' else 'ignored', np])
    result_df = pd.DataFrame(result, columns=['EC2Platform','EC2RIModel', 'EC2NP'])
    return result_df


if __name__ == "__main__":
    dbr_file, ri_sub_file, date_hour = parse_args()
    # 从DBR数据文件生成EC2使用数据集
    df_ec2_usage = create_ec2_dataset(dbr_file, date_hour)
    # 从订阅数据文件生成EC2 RI订阅信息数据集
    df_ri_sub = create_ec2_ri_dataset(ri_sub_file, date_hour)
    # 合并数据集，对EC2NP求和
    df_all = pd.concat([df_ec2_usage,df_ri_sub])
    df_sum = df_all.groupby(['EC2Platform', 'EC2RIModel']).sum()
    # 当EC2NP < 0, 表示需要补足的RI
    print('=' * 17, 'Ondemand', '=' * 17)
    print(df_sum[df_sum['EC2NP'] < 0].sort_values(by=['EC2Platform','EC2RIModel','EC2NP']))
    # 当EC2NP > 0, 表示富余的RI
    print('=' * 17, 'Unused RI', '=' * 17)
    print(df_sum[df_sum['EC2NP'] > 0].sort_values(by=['EC2Platform','EC2RIModel','EC2NP']))
    # 当EC2NP = 0, 表示正好被RI覆盖
    print('=' * 17, 'Used RI', '=' * 17)
    print(df_sum[df_sum['EC2NP'] == 0].sort_values(by=['EC2Platform','EC2RIModel','EC2NP']))