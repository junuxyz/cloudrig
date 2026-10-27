from cloudrig.broker import BrokerProvider


def connect(name: str) -> BrokerProvider:
    return BrokerProvider(name)
