import {test, assertEqual} from './harness.js';
import {PrivateBus, FakeRecordingService} from './privateBus.js';

const NAME = 'dev.meetingscribe.Service';
const PATH = '/dev/meetingscribe/Service';

test('a second fake cannot own an already owned name: appear() rejects promptly', async () => {
    const bus = await PrivateBus.start();
    const first = new FakeRecordingService(await bus.connect(), NAME, PATH);
    const second = new FakeRecordingService(await bus.connect(), NAME, PATH);
    try {
        await first.appear();
        let error = null;
        const startedAt = Date.now();
        try {
            await second.appear();
        } catch (e) {
            error = e;
        }
        assertEqual(error !== null, true, 'appear() must reject: ');
        assertEqual(Date.now() - startedAt < 2500, true, 'appear() must reject promptly: ');
    } finally {
        second.vanish();
        first.vanish();
        await bus.stop();
    }
});
