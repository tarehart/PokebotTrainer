from time import sleep

from rlbot.agents.base_script import BaseScript


# Extending the BaseScript class is purely optional. It's just convenient / abstracts you away from
# some strange classes like GameInterface
class SampleScript(BaseScript):
    def run(self):
        while True:
            packet = self.get_game_tick_packet()
            self.renderer.begin_rendering()
            for i in range(packet.num_cars):
                car = packet.game_cars[i]
                self.render_value(5, 100, 6 + i, f'{car.spawn_id} {car.name}', i)
            self.renderer.end_rendering()
            sleep(.2)

    def render_value(self, x, y_basis, index, label, value):

        if type(value) is bool:
            if value:
                color = self.renderer.cyan()
            else:
                color = self.renderer.create_color(255, 255, 200, 200)
        else:
            color = self.renderer.white()

        text = f"{label}: {value}"
        if type(value) is float:
            text = f"{label}: {value:.2f}"

        self.renderer.draw_string_2d(x, y_basis + index * 35, 2, 2, text, color)


# You can use this __name__ == '__main__' thing to ensure that the script doesn't start accidentally if you
# merely reference its module from somewhere
if __name__ == '__main__':
    print("Spawn diagnosis starting...")
    script = SampleScript("Spawn Diagnosis")
    script.run()
