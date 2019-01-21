# AWS中国区RI使用统计工具

### 介绍

云成本管理是上云后的一个重要问题，毕竟灵活性比传统的物理部署大多了，因此也更复杂些。自打2015年开始使用中国区的EC2，一直就觉得它在成本分析方面基本没有提供什么工具，很多东西只能自己动手。对于大多数初期上云的人来说，预留实例（Reserved Instance，或者按国内通俗的叫法：“包年”）对其成本管理影响很大。

AWS中国的管理控制台没有提供像全球区那样提供成本分析工具，因此国内用户目前只能通过Support服务获取相关数据，费时费力。话说现在9102年了，还常常能听到相关的抱怨，想到以前踩过的那些坑，开了这个项目分享些经验，希望对还在对困惑的同行有点帮助。

### 思路说明

#### AWS EC2 RI 匹配规则

AWS中国区RI新手三连：

> “我买的RI**匹配上了**么？”
>
> “RI匹配到**哪个机器**上了？”
>
> 后来机器多了，还会问“还有哪些机器**需要买RI**？”

机器规格变动比较频繁的应该还会有第四问：

> “规格调整后哪些RI是**富余**了可以再利用，哪些还需要**补充**”。

这些问题的背后都本质是对AWS EC2 RI匹配规则的理解。

AWS EC2 RI匹配原则，其实官方文档上应该是有比较明确的说明的 [传送门>>](https://docs.aws.amazon.com/zh_cn/AWSEC2/latest/UserGuide/apply_ri.html)

在这些规则中有一些问题需要特别注意：

* 与大家直觉不同的是，AWS采用了一种相对智能的动态匹配方式，而不是“某个RI订阅直接针对某个EC2实例”这样的静态一一对应模式，目的显然是最大化利用好每个RI，但是这种好心却使得回答上面那几个问题就没那么直接了当了，需要进行一定的计算才能知道。
* 没有启动的机器是不会有RI覆盖的
* Amazon Linux是个特别的存在，RI可以统一折算成标准化因子，因此可以实现部分匹配也可以相互多个RI匹配同一个实例，非常灵活 [详细说明 >>](https://docs.aws.amazon.com/zh_cn/AWSEC2/latest/UserGuide/apply_ri.html#apply-regional-ri)

#### EC2 RI分析

从上文的RI匹配规则看，每个小时的匹配情况都有可能在变化，因此分析的最小精度或者基本单位是**小时**。

分析某个时刻RI使用情况（覆盖、缺失、富余三种），需要两组数据。

* 处于活动状态的EC2实例列表
  
    目前有两个可用的数据源：

    1.  实时：用API或者AWS命令行获取的是实时数据，不过由于缺少EC2的平台信息（目前的API、命令行返回的EC2描述里platform只有windows），无法与订阅列表里的平台对应上，因此除非能够解决判断EC2平台信息的问题，否则实时分析是做不了的。

    1.  非实时：来自于**DBR**（Detailed Billing Report）文件，在AWS控制台打开“接收账单报告”设置后，账单报告（DBR）会写入指定的S3桶，一共四种不同精度的报告，这里使用的是最详细的成为“包含资源和标签的详细账单报告”的那种（nnnnnnnnnnn-aws-billing-detailed-line-items-with-resources-and-tags-ACTS-yyyy-mm.csv.zip）。需要注意的是，数据不是实时的（感觉每隔几小时当月数据会刷新一次），作为账单分析只能做参考，但不影响RI使用状态分析。本项目使用从s3下载的DBR数据文件。

* 有效RI订阅记录

    目前控制台没有提供导出功能，只找到用API或者AWS命令行获取的方法。这个项目使用AWS命令行生成json格式的数据。如何配置命令行请参见官方文档，这里不再赘述。

**所谓RI覆盖、利用率之类的计算，本质就是某个小时里这两组数据的匹配**。



### 关于示例代码

由于数据集的局限性（机型、机器数量、订阅类型、数据规模、关联账户等），也未考虑covertable和spot类的实例数据处理，因此不保证这个示例程序能在您的数据集上正常运行，请利用自己的数据对算法做适应性调整。

另外，从最近几年分析DBR数据的经验看，AWS也会**悄悄**的调整这个数据表的结构和内容，届时对应的处理程序也要做适应性修改。

这里的实例代码只是上述思路利用python下的Pandas数据分析包的一个实现，您大可以使用自己熟悉的工具和环境来解决。由于只是原理性说明，未做严格的数据校验、异常处理等，在真实环境中使用时这些都是需要补的课。鉴于DBR是一个包含极其详尽信息的很有价值的数据源，可以在此基础上做很多分析，实际使用时建议将DBR导入数据库后配合其他分析及展示工具使用。

ps. 本人不是专业的开发人员，算法里要处理的细节比较多，可能会看着乱些，代码质量轻喷哈。



### 环境配置与使用说明

实例程序在python 3.6.x和3.7.x下都可以正常运行，仅仅依赖于pandas包，版本使用的`0.23.4`，低版本的pandas可能会出错。

目前获取RI订阅数据需要用到API或者AWS命令行工具，并配置好访问权限和输出格式（权限方面仅需要有`EC2:DescribeReservedInstances`即可，输出格式推荐配置成json）。




### 运行示例
从S3上下载好DBR数据，导出订阅数据后，运行
```shell
python ./ri-usage-profiler.py -d ./data/1234567890-aws-billing-detailed-line-items-with-resources-and-tags-ACTS-2017-07.csv.zip -s ./data/ri-sub.json -t 2017-07-08/0
```
运行结果
```shell
[v] Date hour you provided(2017-07-08 00:00:00) in date range of DBR data (2017-07-01 00:00:00 - 2017-07-31 23:00:00)
================= Ondemand =================
                        EC2NP
EC2Platform EC2RIModel
Linux/UNIX  ignored     -64.0
Windows     m4.2xlarge   -2.0
            r3.2xlarge   -1.0
            r3.xlarge    -3.0
================= Unused RI =================
                                     EC2NP
EC2Platform              EC2RIModel
Red Hat Enterprise Linux r3.2xlarge    1.0
                         r3.4xlarge    1.0
                         r3.large      2.0
                         r3.xlarge     1.0
Windows                  c4.xlarge     1.0
================= Used RI =================
                                     EC2NP
EC2Platform              EC2RIModel
Red Hat Enterprise Linux r3.8xlarge    0.0
                         t2.small      0.0
Windows                  c4.2xlarge    0.0
                         m3.2xlarge    0.0
                         m3.large      0.0
                         m3.medium     0.0
                         m3.xlarge     0.0
                         m4.large      0.0
                         m4.xlarge     0.0
                         r3.4xlarge    0.0
```